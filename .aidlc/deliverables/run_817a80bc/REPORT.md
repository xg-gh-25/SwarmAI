# # Fix: the Hive's app-layer auth runs a \~1 s bcrypt on the event loop for every… · Audit through DELIVER

Delivered: # Fix: the Hive's app-layer auth runs a \~1 s bcrypt on the event loop for every request \*\*Evidence (measured on a Hive, 2026-11-15).\*\* \`backend/middleware/hive\_auth.py\` (the credential check, around l. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 26m 57s wall · 26m 57s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 4 not run · 9/10 findings closed (4 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 542k comparable · 435k input · 107k output · 13.4M cache reads, not counted · 13.9M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Knowledge: requirement recalled · 8 · design recalled · 5 · cited 1 (+5 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which thread pool runs bcrypt? → A new 'hive\_auth' pool (2 workers) in core.executors.\_POOL\_SPECS. Why: core/executors.py is the backend's rule that blocking work over 1 s never goes to the default pool; a dedicated pool also means wrong-password floods (never cached) queue only behind each other (DESIGN\_SPEC, agent)
- D2 · Cache lifetime and size → 600 s from the successful verification, not refreshed on hit; at most 32 entries, oldest evicted. Why: A Hive is single-tenant with one credential, so 32 is far above real use while bounding memory; 600 s caps how long a credential stays accepted in-process after a rotation that reuses the same hash (a rotation that changes the hash takes effect at once) (DESIGN\_SPEC, agent)
- D3 · Is a cache entry per middleware instance or per module? → Per HiveAuthMiddleware instance. Why: Starlette builds the middleware stack once per app, so production has one instance; tests get a fresh cache per app without global resets (DESIGN\_SPEC, agent)
- D4 · What happens to an in-flight task that belongs to another event loop? → It is ignored and a new check starts on the running loop. Why: Awaiting a task of a closed loop would raise and deny that credential forever; production has one loop, test clients may use several (DESIGN\_SPEC, agent)
- D5 · Two tests outside the plan stub HiveAuthMiddleware.\_is\_authorized with a sync lambda; the async method breaks them → Convert both stubs to async functions. Why: They are consumers of the changed contract (R27); keeping a sync/async dual path in the middleware would add complexity only for tests (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Run verify\_password through asyncio.to\_thread on every request, no cache. Not taken: Still about 1 s of latency on every request, and it occupies asyncio's default executor, which core/executors.py exists to keep free for /health and the chat path; a burst would starve it.
- A3 · Lower the bcrypt cost of HIVE\_PASS\_HASH, or switch the app layer to a fast hash. Not taken: The task forbids it: the agent on the Hive can read the hash, so the cost is what protects the password; the hash is also shared with Caddy.
- A4 · Cache keyed by plain sha256(password) and no single-flight. Not taken: A plain unsalted fast hash in memory is a cheap offline-guessing target if memory or a log leaks it; and a browser's first load sends many parallel requests with the same credential, each of which would run its own 1 s bcrypt on a 2-thread pool.

Requirements analysis:

- missing\_edge (R1.2): A browser's first page load sends many parallel requests with the same credential; without single-flight each would start its own 1 s bcrypt. → rewritten
- missing\_edge (R3.2): Running bcrypt via asyncio.to\_thread would occupy the default executor that chat-path code also awaits; a burst of first checks (or wrong passwords, which are never cached) could starve it. → rewritten
- missing\_edge (R2.3): The cache key is derived from the presented password; it is an HMAC with a per-process random key bound to the expected hash, so the in-memory key is not a fast unsalted hash of the password and a hash change can never match an old entry. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 10/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ d60c3e527d87: 4 pass, 0 fail, 0 not run
- test · swarmai @ f7581a29c81c: 9 pass, 0 fail, 4 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_817a80bc49c1/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_hive\_auth.py tests/test\_executors.py -q --timeout=60 → pass (35 passed)
- perl -e 'alarm 110; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_817a80bc49c1/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_skill\_health.py::TestHealthEndpointFailSafe tests/te → pass (consumers of \_is\_authorized)
- python scripts/smoke\_import.py → pass (exit 0)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (exit 0)
- python scripts/lint\_skills.py → pass (exit 0)
- python scripts/lint\_experts.py → pass (exit 0)
- bash scripts/sync-version.sh check → pass (exit 0)
- bash scripts/run\_bvt.sh → pass (860 tests passed (with the task's uv on PATH; without it test\_precommit\_uv\_lock cannot find uv))
- npx tsc --noEmit → not\_run (the change touches no desktop file (backend middleware, executors and tests only); run through task\_checks on the task branch after landing)
- 3 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: the change touches no desktop file (backend middleware, executors and tests only); run through task\_checks on the task branch after landing
- swarmai · npm run lint:contract: the change touches no desktop file (backend middleware, executors and tests only); run through task\_checks on the task branch after landing
- swarmai · npm run build: the change touches no desktop file (backend middleware, executors and tests only); run through task\_checks on the task branch after landing
- swarmai · npx vitest run --reporter=verbose: the change touches no desktop file (backend middleware, executors and tests only); run through task\_checks on the task branch after landing
