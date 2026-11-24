# ## Goal: an Elastic Workers run keeps what it produced when its backend dies, a… · Audit through DELIVER

Delivered: ## Goal: an Elastic Workers run keeps what it produced when its backend dies, and the Host learns of it in minutes, not 8 hours Today on AgentCore (Elastic Workers): - The worker uploads each record (. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 41m 22s wall · 41m 22s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 8 not run · 1 red at base · 3/4 findings closed (3 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 853k comparable · 649k input · 204k output · 40.1M cache reads, not counted · 41.0M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 1 (+6 found on demand) · searched the DDD 3×

## Decisions

- D1 · When is a heartbeat stale? → now - newest(heartbeat.at, crashed state.at) &gt; heartbeat every + the runtime idle timeout (900 s) + END\_MARGIN\_S. Why: an idle but live backend writes no heartbeat until the platform stops its session after the idle timeout; the margin covers clock skew, as END\_MARGIN\_S does elsewhere (DESIGN\_SPEC, agent)
- D2 · Which S3 ends shorten writes\_end\_at? → expired (the supervisor sealed before the limit), crashed with restarting false, and a stale heartbeat; recorded with the time this Host read them. Why: after these nothing in the round writes again; an end decided by age alone keeps the lifetime-based bound (the microVM may still write in its last minutes) (DESIGN\_SPEC, agent)
- D3 · Which run-state reason do the new ends show? → expired -&gt; carrier\_expired; crashed final and a stale heartbeat -&gt; idle. Why: the existing reasons check\_due, settle and the desktop already handle; no new UI text (DESIGN\_SPEC, agent)
- D4 · How much before the 8-hour limit is the pre-limit seal? → PRE\_LIMIT\_MARGIN\_S = 1200 s from the supervisor's start time. Why: stopping the backend (up to 30 s) plus the rescue's hang guard (900 s) must finish before the platform ends the session (DESIGN\_SPEC, agent)
- D5 · What does the pre-limit seal do when the backend is idle? → nothing: an idle backend has no open turn and nothing pending. Why: busy() is the supervisor's own measure of what is still to be produced; an unknown answer counts as busy and is sealed (DESIGN\_SPEC, agent)
- D1 · Where does the rescue open the database? → only when data.db already exists, migrations only, as the lifespan opens a returning store. Why: the smoke run showed that opening a missing one creates an empty file, which the restarted backend would take for a returning user's store and skip its schema (BUILD report · swarmai, agent)
- D2 · What does a crashed state.json with restarting true read as on the Host? → unknown / starting, an existing reason. Why: the backend is starting again; staleness of its at still ends the round if it never comes back (BUILD report · swarmai, agent)
- D3 · Fix the pre-existing red lifespan test in this change? → yes, its assertion only. Why: it tests core/hive\_boot, which this change extends, and was red on the base for a stale distance check; main.py is unchanged (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Supervisor only restarts the backend and lets the restarted backend's own boot cleanup (main.py lifespan close\_open\_turns) and export lane seal and upload. Not taken: the seal then depends on the backend booting again; a backend that dies at boot (the same OOM, a bad state) seals nothing and the pre-limit seal has no backend to rely on; the task orders upload and seal before the restart
- A3 · Supervisor writes the heartbeat from its own /ping status. Not taken: the supervisor has no AWS client in its process (the snapshot rule) and every write would be a boto3 subprocess every few minutes; the backend already has an S3 lane that runs each 20 s pass
- A4 · Fold the heartbeat into state.json, rewritten every few minutes. Not taken: an old image's state.json also carries at but is rewritten only on change, so the Host could not tell a live old-image round from a dead one; a separate object present only on new images makes the staleness rule opt-in by its existence

Requirements analysis:

- missing\_edge (R2.1, R2.5): an image built before this change writes no heartbeat, and a role policy not yet refreshed may refuse heartbeat.json; a missing heartbeat read as stale would end every old-image round at once → rewritten
- missing\_edge (R2.2): a backend idle but alive writes no heartbeat until the platform stops it after its idle timeout (900 s); the stale threshold therefore adds the idle timeout and clock margin to the heartbeat cadence → rewritten
- missing\_edge (R2.3, R2.2): an end decided by age (carrier\_expired) must not shorten writes\_end\_at: the microVM may still write until opened\_at + MAX\_RUN\_S + WRITE\_GRACE\_S; only the S3 ends that say nothing more is written (expired, crashed final, stale heartbeat) shorten it → rewritten
- missing\_edge (R1.1): a rescue runs while no backend runs, so it never races a live turn writer; core.hive\_boot only touches turns started before its own process started → confirmed
- missing\_edge (R1.4): the pre-limit seal needs time to stop the backend, bundle and upload: PRE\_LIMIT\_MARGIN\_S exceeds the stop wait plus the rescue's hang guard → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 9/26 tool calls
- correctness · review r1: read 100% of its prompt · 18/26 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 13/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ f54fab53e44b: 4 pass, 0 fail, 0 not run
- test · swarmai @ 5ff2b44fff48: 4 pass, 0 fail, 8 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- perl -e 'alarm 290; exec @ARGV' .venv/bin/python -m pytest tests/test\_agentcore\_supervisor\_crash.py tests/test\_hive\_rescue.py tests/test\_elastic\_round\_liveness.py tests/test\_carriers\_agentcore.py test → pass (246 tests: the change's execution tests and the existing tests of every changed function and its direct callers, on this tree)
- python scripts/smoke\_import.py → pass (698 modules import)
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- bash scripts/run\_bvt.sh → pre\_existing (82 files; every test passes but the 3 of tests/test\_precommit\_uv\_lock.py, which need a uv binary this machine does not have (shutil.which('uv') is None) and fail the same way on the run's base (pipeli)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_34d8892b → not\_run (environment: the installed SwarmAI reads features.yaml schema 1 and the repo's is 2 ("update SwarmAI"); no walk could be read)
- python scripts/lint\_skills.py → not\_run (no skill file changed)
- python scripts/lint\_experts.py → not\_run (no expert file changed)
- npx tsc --noEmit → not\_run (no desktop file changed (no UI string added))
- npm run lint:contract → not\_run (no desktop file changed)
- 3 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 82 files; every test passes but the 3 of tests/test\_precommit\_uv\_lock.py, which need a uv binary this machine does not have (shutil.which('uv') is None) and fail the same way on the run's base (pipeline-v1 base-tree)

## Not run (the agent's reason, not checked)

- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_34d8892b: environment: the installed SwarmAI reads features.yaml schema 1 and the repo's is 2 ("update SwarmAI"); no walk could be read
- swarmai · python scripts/lint\_skills.py: no skill file changed
- swarmai · python scripts/lint\_experts.py: no expert file changed
- swarmai · npx tsc --noEmit: no desktop file changed (no UI string added)
- swarmai · npm run lint:contract: no desktop file changed
- swarmai · npm run build: no desktop file changed
- swarmai · bash scripts/sync-version.sh check: no version file changed
- swarmai · npx vitest run --reporter=verbose: no desktop file changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/hive\_rescue.py
- swarmai: backend/tests/test\_agentcore\_supervisor\_crash.py
- swarmai: backend/tests/test\_elastic\_round\_liveness.py
- swarmai: backend/tests/test\_hive\_rescue.py
