# ## Goal: on Windows and Linux the backend exits by itself after /shutdown, so t… · Audit through DELIVER

Delivered: ## Goal: on Windows and Linux the backend exits by itself after /shutdown, so the desktop app's shutdown does not wait its full ceiling On Windows and Linux the backend runs as a child process of the. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 30m 17s wall · 30m 17s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 3 not run · 1 red at base · 3/5 findings closed (1 test-closed, confirmed by review) · 2 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 439k comparable · 335k input · 105k output · 15.2M cache reads, not counted · 15.7M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 3 (+1 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which modes exit after /shutdown? → Only SWARMAI\_MODE=subprocess (the Tauri child). Why: daemon and hive have a supervisor that owns restarts; dev is a manually started backend and keeps its behaviour (DESIGN\_SPEC, agent)
- D2 · How long may the teardown run? → 2.0 s, then it is cancelled; the child sweep waits at most 0.5 s more. Why: The app's CHILD\_EXIT\_CEILING is 3 s from the moment /shutdown answers; 2.0 + 0.5 leaves margin. Hooks enqueued by disconnect\_all got about 3 s before the app's kill today, so the budget does not cut them shorter in a material way (DESIGN\_SPEC, agent)
- D3 · How does the process exit? → os.\_exit(0) after logging.shutdown() and a stdout/stderr flush. Why: A normal interpreter exit joins non-daemon threads (a cancelled to\_thread join, executor workers) and could hang past the ceiling; the teardown already did the cleanup (DESIGN\_SPEC, agent)
- D4 · Which children are killed before the exit? → The LifecycleManager's tracked PIDs, then every live descendant of the backend's own pid, deepest first, using the existing session\_unit psutil helpers (create-time checked). Why: In child mode every descendant is the backend's own and would be orphaned to init on Linux; the teardown can be cancelled before disconnect\_all or the service manager reach them (DESIGN\_SPEC, agent)
- D5 · Sync or async background task for the scheduler? → async. Why: The first real-backend measurement showed Starlette runs a sync background task in its thread pool, where get\_running\_loop() fails (no exit happened). Caught by the E2E run, fixed, and a test that drives the real scheduler over HTTP now fails on the sync version (mutation checked) (BUILD report · swarmai, agent)
- D6 · How do the last log lines survive os.\_exit? → Stop the QueueListener (drains the queue) before logging.shutdown() in \_exit\_process. Why: os.\_exit skips the atexit hook that normally stops it; the first measurement lost the final log lines (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Ask uvicorn to stop (raise SIGINT / set should\_exit) and let the normal lifespan shutdown and interpreter exit end the process. Not taken: Unbounded: uvicorn waits for open connections (SSE streams) with no graceful timeout, the lifespan drains hooks for up to 10 s, the interpreter exit joins non-daemon executor threads, and uvicorn re-raises the captured SIGINT after serve() (a KeyboardInterrupt traceback in desktop\_main). On Windows os.kill is TerminateProcess, so no teardown at all.
- A3 · Leave the backend as is and shorten the app's wait in lib.rs. Not taken: Keeps the forced tree kill as the only stop and never runs the backend's graceful teardown, which is what the task asks to fix.

Requirements analysis:

- missing\_edge (R1.2): A Linux exit that leaves the CLI or MCP processes alive re-parents them to init (orphans); the teardown may also be cancelled before disconnect\_all reaches them. → rewritten
- missing\_edge (R3.1): The lifespan shutdown drains post-session hooks for up to 10 s and joins the ingestion worker, so it can exceed the app's 3 s ceiling; Python's normal interpreter exit could also block on non-daemon executor threads. → rewritten
- missing\_edge (R2.2): dev mode also allows /shutdown; the task limits the exit to the app's child (subprocess) mode, so dev keeps its behaviour. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 14/25 tool calls
- correctness · review r1: read 100% of its prompt · 16/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- security · review r2: read 100% of its prompt · 11/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 00e73d870156: 4 pass, 0 fail, 0 not run
- test · swarmai @ 272d9dbfc34d: 7 pass, 0 fail, 3 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not_run
  - desktop-contract-lint: not run
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- perl -e 'alarm 140; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_f9e0ffa21136/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_child\_mode\_shutdown\_exit.py --timeout=120 -p no:cach → pass (17 passed)
- perl -e 'alarm 280; exec @ARGV' python -m pytest tests/test\_child\_mode\_shutdown\_exit.py tests/test\_lifecycle\_watchdog.py tests/test\_graceful\_cli\_close.py tests/test\_daemon\_sidecar\_fixes.py tests/test\_ → pass (153 passed)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pre\_existing (1347 BVT tests run (also re-run with --maxfail=1000): the only failures are the 3 tests of tests/test\_precommit\_uv\_lock.py, which fail identically on the run's base tree because this machine has no \`u)
- python -m scripts.feature\_map replay --affected --run run\_f9e0ffa2 → not\_run (environment: all 16 reached walks not\_run, runtime unavailable (the pinned Chromium lacks libX11/libnss3/... system libraries and this machine has no swarm-replay user); the installed SwarmAI CLI also)
- npx tsc --noEmit → not\_run (no TypeScript changed: desktop's only change is in src-tauri/src/lib.rs (a header argument on two shutdown requests and comments); no cargo toolchain on this machine to compile it)
- npm run build → not\_run (the vite build does not read src-tauri; no frontend file changed)
- 1 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 1347 BVT tests run (also re-run with --maxfail=1000): the only failures are the 3 tests of tests/test\_precommit\_uv\_lock.py, which fail identically on the run's base tree because this machine has no \`uv\` executable on PATH (shutil.which('uv') is None -&gt; TypeError in subprocess)

## Not run (the agent's reason, not checked)

- swarmai · python -m scripts.feature\_map replay --affected --run run\_f9e0ffa2: environment: all 16 reached walks not\_run, runtime unavailable (the pinned Chromium lacks libX11/libnss3/... system libraries and this machine has no swarm-replay user); the installed SwarmAI CLI also reads only features.yaml schema 1
- swarmai · npx tsc --noEmit: no TypeScript changed: desktop's only change is in src-tauri/src/lib.rs (a header argument on two shutdown requests and comments); no cargo toolchain on this machine to compile it
- swarmai · npm run build: the vite build does not read src-tauri; no frontend file changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_child\_mode\_shutdown\_exit.py
