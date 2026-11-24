# Add a concurrent batch dispatch (route, CLI, per-batch Brain/bundle cache, dedi… · Audit through DELIVER

Delivered: Add a concurrent batch dispatch (route, CLI, per-batch Brain/bundle cache, dedicated pools, start timing), print validation fields in the CLI, and let session\_worktree remove/land a branch whose conte. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 31m 40s wall · 31m 40s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 3 not run · 3/3 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 667k comparable · 524k input · 143k output · 35.0M cache reads, not counted · 35.7M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · How many batch items run at once → BATCH\_CONCURRENCY = 4, the same as the dedicated elastic start pool. Why: a bound that keeps the Host responsive (git bundle, Brain package, HTTP and a start thread per item), not a cost cap; ten items take about three waves of \~20 s (DESIGN\_SPEC, agent)
- D2 · What deadline a batch item has → all items share one DISPATCH\_DEADLINE\_S counted from the batch's start; an item whose turn comes with less than the session window left fails before anything is asked, saying nothing was sent. Why: keeps the answer under the CLI's DISPATCH\_TIMEOUT\_S, the contract tied by tests/test\_swarm\_out\_deadline.py (DESIGN\_SPEC, agent)
- D3 · How a batch item's spec is validated → the route takes items as raw objects and validates each with DispatchBody; a validation error is that item's result (its fields), not a 400 for the batch. Why: one bad item (a task over 20000 characters) must not stop the others (DESIGN\_SPEC, agent)
- D4 · Which pools → new executors pools 'elastic\_start' (4) for InvokeAgentRuntime start and StopRuntimeSession, 'elastic\_s3' (4) for the round-end check before every request to a round. Why: a start holds a thread \~17 s; the default pool schedules the chat path and /health (DDD ebdcefd8); the per-request S3 checks must not queue behind starts (DESIGN\_SPEC, agent)
- D5 · What land answers for a squash-landed branch → ok, landed false, already\_landed true, without rebasing or moving any ref. Why: the content is in base; rebasing conflicts because the squash has another patch-id (DESIGN\_SPEC, agent)
- D6 · Should /dispatch share the batch's argument mapping → yes: \_dispatch\_args builds dispatch\_spec's arguments for both routes. Why: a field added to /dispatch later reaches the batch too, instead of silently not (BUILD report · swarmai, agent)
- D7 · How remove deletes a squash-landed branch → git branch -D, only after \_content\_in\_base said its content is in base. Why: branch -d refuses a branch that is not an ancestor, after the worktree was already removed (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Asynchronous dispatch: the route queues the tasks and returns at once, the CLI polls a status route. Not taken: the person chose a synchronous dispatch bounded by one deadline under the CLI's wait (DDD 6c00b80b); a queue is new job state lost on a daemon restart and changes the card contract
- A3 · Each batch item gets its own full DISPATCH\_DEADLINE\_S and the CLI waits ceil(n/concurrency) deadlines. Not taken: the CLI's wait is bounded by the agent's 10-minute Bash ceiling; a longer wait turns into maybe\_sent for every item
- A4 · Thread the cache through dispatch\_spec, \_dispatch\_round and \_ship\_repos as explicit parameters. Not taken: touches four signatures that other runs edit at the same time; a ContextVar set by the batch is read only at the two places that package, and is absent for a single dispatch
- A5 · remove judges landed by git cherry (patch-id equivalence). Not taken: a squash of several commits has a different patch-id than each of them, so cherry still says unmerged; the tree check answers the real question: would merging the branch change base

Requirements analysis:

- missing\_edge (R1.7): a batch of many slow items cannot each get 480 s: the CLI waits 540 s under a 10 minute Bash ceiling; the items share one deadline and a late item fails with nothing sent → rewritten
- missing\_edge (R1.4): a shared bundle file must not be deleted by the first item that uploaded it; the batch cache owns and removes the files → rewritten
- missing\_edge (R1.3): dispatch-many matches the card's literal-dispatch regex (dispatch\\b) and would draw a bogus failure card; the regex must exclude dispatch-many, its rows draw cards from the dispatch index → rewritten
- missing\_edge (R1.2): concurrent appends to dispatches.jsonl from worker threads may interleave long rows; \_append takes a lock → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 11/26 tool calls
- correctness · review r1: read 100% of its prompt · 16/26 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- security · review r2: read 100% of its prompt · 3/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 938d2047e525: 6 pass, 0 fail, 0 not run
- test · swarmai @ b5f87bc7f39e: 6 pass, 0 fail, 3 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_cec18195738e/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_batch.py tests/test\_session\_worktree\_squash.py tests/test\_elastic\_dispatch → pass
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/run\_bvt.sh → not\_run (the broad BVT allowlist does not list any file this change touches (it is fs/subprocess-free logic only); it runs once through task\_checks on the landed branch)
- npx tsc --noEmit; npm run lint:contract; npm run build; npx vitest run → not\_run (the change touches no desktop file and adds no UI string; the run worktree has no node\_modules. They run through task\_checks on the landed branch)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_cec18195738e/repos/swarmai/backend/.venv/bin/python -m scripts.feature\_map replay --affected --run run\_cec18195 → not\_run (environment: this machine has no Chromium system libraries (libX11, libnss3, ...) and no swarm-replay user, so all 16 reached walks report not\_run 'runtime unavailable'; the installed /opt/swarmai fea)

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the broad BVT allowlist does not list any file this change touches (it is fs/subprocess-free logic only); it runs once through task\_checks on the landed branch
- swarmai · npx tsc --noEmit; npm run lint:contract; npm run build; npx vitest run: the change touches no desktop file and adds no UI string; the run worktree has no node\_modules. They run through task\_checks on the landed branch
- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_cec18195738e/repos/swarmai/backend/.venv/bin/python -m scripts.feature\_map replay --affected --run run\_cec18195: environment: this machine has no Chromium system libraries (libX11, libnss3, ...) and no swarm-replay user, so all 16 reached walks report not\_run 'runtime unavailable'; the installed /opt/swarmai feature\_map is older than the repo's features.yaml schema 2

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_session\_worktree\_squash.py
- swarmai: backend/tests/test\_swarm\_out\_batch.py
