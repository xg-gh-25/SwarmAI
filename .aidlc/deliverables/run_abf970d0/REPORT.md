# ## Goal: a dispatched run's lessons are sealed when its work ends, on both carr… · Audit through DELIVER

Delivered: ## Goal: a dispatched run's lessons are sealed when its work ends, on both carriers, and never while a pipeline decision still waits Two problems in the same function. 1) EC2 runs carry a fixed \~62 mi. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 23m 31s wall · 23m 31s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 red at base · 1/4 findings closed (7 reported; within 10 lines of one file they count once) (1 test-closed, confirmed by review) · 1 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 594k comparable · 472k input · 123k output · 23.8M cache reads, not counted · 24.4M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 0 (+4 found on demand) · searched the DDD 3×

## Decisions

- D1 · How is a dispatched session told from an interactive one on a Hive? → a prepared work order whose session\_id is the session (hive\_workspace.bind at the session's first event), or the AgentCore mode (one dispatched session per microVM). Why: every current dispatch carries a work order (swarm\_out REQUIRED\_CAPS work\_orders); hive\_boot.bound\_sessions already reads it this way (DESIGN\_SPEC, agent)
- D2 · Where is a pending decision read? → pipeline\_human.owned\_runs(session\_id, status='awaiting\_human'): the run.json projection through run\_index. Why: the status the decisions mirror (swarm\_out\_decisions) also starts from; no new state (DESIGN\_SPEC, agent)
- D3 · What if the runs cannot be read? → count as waiting (no review, no closed), log a warning. Why: as \_review\_open: an unknown answer keeps the results; the idle hook asks again later (DESIGN\_SPEC, agent)
- D4 · Does a deleted session honour the guard? → no: on\_session\_deleted still seals lessons and closed. Why: nothing can answer a deleted session's decision (DESIGN\_SPEC, agent)
- D5 · Should an AgentCore microVM stay up while a decision waits? → yes: busy reports waiting\_input and state.json says waiting. Why: otherwise the microVM idles out 15 min later with the decision unanswerable and the record unclosed; the DDD gap entry asks to count it as waiting (DESIGN\_SPEC, agent)
- D6 · Where does the idle hook's closed check read the workspace? → hive\_records.\_workspace (SwarmWS, as hive\_agentcore.\_workspace); the AgentCore test fixture patches both. Why: one carrier-neutral source (BUILD report · swarmai, agent)
- D7 · Which sessions does the AgentCore busy check read decisions for? → the live units' sessions plus the exported record's session. Why: a Brain synced to the microVM may carry run.json files of the Host's sessions; reading only this microVM's sessions never holds it for those (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep request\_review/review\_wanted in hive\_agentcore and widen its active() gate to every Hive. Not taken: hive\_agentcore's contract is 'inert outside the AgentCore mode'; widening it there mixes EC2 behaviour into the microVM module and its busy/S3 machinery
- A3 · Lower s2\_jobs.REVIEW\_DELAY on a Hive. Not taken: changes interactive Hive sessions too (U1), and does nothing about a run waiting on a decision

Requirements analysis:

- missing\_edge (R1.1): An AgentCore Hive session that no work order names (a bind that failed) used to get the immediate review; keep it: on AgentCore every session is the microVM's dispatched one. → rewritten
- missing\_edge (R2.2): A review job requested before the decision appeared (an earlier turn) can end while the decision waits; the guard therefore also sits where \`closed\` is sealed, not only where the review is requested. → rewritten
- missing\_edge (R2.4): An unreadable run store: fail toward not closing, as hive\_agentcore.\_review\_open counts an unreadable job store as open. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 12/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 10/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- security · review r2: read 100% of its prompt · 12/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 795016354b4a: 3 pass, 0 fail, 0 not run
- test · swarmai @ 59953b30b3d0: 2 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: not run
  - backend-problem-values: not run
  - backend-security-scan: not run
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_abf970d00193/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_hive\_dispatched\_review.py -q --timeout=100 --maxfail=1000 -p no:cacheprovider → pass (13 passed (part of the run below); red on the base before the fix (8 failed))
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_abf970d00193/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_cdf\_s2\_jobs.py tests/test\_hive\_records.py tests/test\_hive\_files.py tests/test\_cdf\_ex → pre\_existing (208 tests; the one failure is test\_hive\_boot\_close\_turns.py::test\_lifespan\_closes\_turns\_before\_claims\_and\_bridge, a source-text check of main.py (untouched here) that fails the same way on the base tr)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_abf970d00193/repos/swarmai/backend/.venv/bin/python -m scripts.mutation\_check /tmp/abf/mF4.json --timeout 100 -- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_a → pass (the TEST-stage fix (engine from the protected contract, not find\_run\_dir) reverted: caught, test\_real\_v1\_run\_waiting\_holds\_the\_review red on the forged duplicate)

## Red at the run's base too (the agent's report, not checked)

- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_abf970d00193/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_cdf\_s2\_jobs.py tests/test\_hive\_records.py tests/test\_hive\_files.py tests/test\_cdf\_ex: 208 tests; the one failure is test\_hive\_boot\_close\_turns.py::test\_lifespan\_closes\_turns\_before\_claims\_and\_bridge, a source-text check of main.py (untouched here) that fails the same way on the base tree /tmp/swarmai-base/run\_abf970d0/swarmai; every other test passes

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/hive\_review.py
- swarmai: backend/tests/test\_hive\_dispatched\_review.py
