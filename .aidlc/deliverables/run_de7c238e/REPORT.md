# XG: "把已知的gaps 点全修了" — close the known gaps left by run\_88215315 (the factory-li… · Audit through DELIVER

Delivered: XG: "把已知的gaps 点全修了" — close the known gaps left by run\_88215315 (the factory-line Pipelines Line / Eval and the Swarm Out dispatch track, landed as e7508df2): (1) Hive pipeline runs show no live stage. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 41m 9s wall · 41m 9s active · 27.0% budget |
| Quality | WARN | 1/1 verification · 3 not run · 7/8 findings closed (9 reported; within 10 lines of one file they count once) (6 test-closed, confirmed by review) · tests not run again after main moved · +2 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 518k comparable · 397k input · 121k output · 57.2M cache reads, not counted · 57.7M billed · host-session responses in the run window · 12.7% budget |

Project Agent: SwarmAI · code-change · 11 checks · 0 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 1 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · Which stage is a Hive run in? → The newest pipeline\_stage block of one of the dispatch's run ids (core.swarm\_out.pipeline\_run\_ids, else its project\_agent.run\_id): its stage when in\_progress, its next when completed; a stage outside the known pipeline stages is dropped. Why: Those are the backend-written blocks the Hive persists as stages open and close (DESIGN\_SPEC, agent)
- D2 · How often is it read? → At most once per LIVE\_STAGE\_S (90 s) per dispatch, only for a running pipeline task on a running Hive, one turn (turns=1), at most two at once. Why: A running pipeline turn can be large; a stage changes every few minutes (DESIGN\_SPEC, agent)
- D3 · What counts as new knowledge? → The cognition units of the run's artifacts/reflect.json (decisions, corrections, practices, and delivered rows of kind cognition): what the run submitted to CDF; null before reflect.json exists. Why: CDF admission happens later and its unit ids do not join to the run (decision 7f478aa0); the label says submitted (DESIGN\_SPEC, agent)
- D4 · Where does the Swarm Out funnel word go? → It is the track's label (testid swarm-out-row-funnel kept on it); the status column no longer repeats it. Why: One fact, one representation (DESIGN\_SPEC, agent)
- D5 · Cost of deriving older runs on every analytics call → Keep the derived models per (run id, updated\_at) in memory. Why: Measured 282 ms per call for 26 runs; a run without the fields gets no new event, so its derivation never changes (BUILD report · swarmai, agent)
- D6 · The Swarm Out label for a live stage in a narrow row → The stage name alone; 'ON HIVE · STAGE' on hover. Why: The bars already stand at ON HIVE; the longer label was cut (BUILD report · swarmai, agent)
- D7 · The open finding that 'new' could differ from what was submitted → Fixed in TEST: one function, report\_units, decides the submitted rows for both enqueue\_report and the count. Why: Two readers of the same report with different rules is the defect; a later edit of reflect.json in the workspace remains the same exposure every run document has (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep the live stage in the kept result record (state/swarm-out/results/&lt;id&gt;.json). Not taken: The result check loads, edits and saves that file; a second writer would lose one of the two updates, and the live stage is transient (a minute old is stale), so memory is the right lifetime.
- A3 · Make the Hive push its current stage (a new Hive endpoint or field). Not taken: Needs a Hive release and redeploy; the Hive already persists the in-progress turn's pipeline\_stage blocks, which the existing messages API returns.
- A4 · Backfill old runs by re-projecting them (write\_projection) once. Not taken: A write over every old run.json from a read path, for a window that empties itself in 30 days; deriving read-only in the summary has no write and no migration.

Requirements analysis:

- missing\_edge (R1.1): Results are checked only for stopped runs; a running pipeline run spans one long turn. The in-progress turn is persisted incrementally (the local messages API returned the current turn's tool\_use blocks), so its blocks are readable while it runs. → rewritten
- missing\_edge (R2.1): 'new' counts the units the run submitted to CDF; CDF's own admission happens later and is not read here. The label says what it counts. → rewritten
- missing\_edge (R3.1): Writing run.json from a read path would be lost on the next write\_projection and is a write the analytics route never did; it stays read-only. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 14/26 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 3/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 0b04d724f4d9: 11 pass, 0 fail, 0 not run
- test · swarmai @ 8ba00a35bb62: 10 pass, 0 fail, 3 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not_run
  - backend-bvt: pass
  - frontend-run-frontend-tests: pass

TEST · swarmai:
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest tests/test\_stage\_timeline.py tests/test\_swarm\_out\_live.py tests/test\_cdf\_v2\_sources\_s3.py tests/test\_cdf\_gap\_close.py  → pass (94 passed on this tree: the review's fixes (report\_units takes any Mapping; the import is gone) and every test of the shared rule)
- bash scripts/run\_bvt.sh (its 54 manifest files run in three parts with the repo venv: the worktree has no .venv) → pass (900 passed (299 + 203 + 398) (run on the previous tree; since then s3\_run.py changed by two lines, covered by the run above; no BVT file tests it))
- npx vitest run (all 457 test files, in six parts) → pass (5171 passed (run on the previous tree; since then only backend/core/ingestion/sources/s3\_run.py and a test changed: two lines, both covered by the run above))
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass (all modules import)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (clean (run on the previous tree; since then only backend/core/ingestion/sources/s3\_run.py and a test changed: two lines, both covered by the run above))
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pass (PASS)
- npx tsc --noEmit → pass (clean (run on the previous tree; since then only backend/core/ingestion/sources/s3\_run.py and a test changed: two lines, both covered by the run above))
- npm run lint:contract → pass (clean (run on the previous tree; since then only backend/core/ingestion/sources/s3\_run.py and a test changed: two lines, both covered by the run above))
- npm run build → pass (built under the bundle cap (run on the previous tree; since then only backend/core/ingestion/sources/s3\_run.py and a test changed: two lines, both covered by the run above))
- scripts.mutation\_check (the TEST change) -- pytest tests/test\_stage\_timeline.py → pass (caught: counting rows without the submission rule turns the test red)
- 3 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main e7508df2fcfa-&gt;3d8db9317fbc): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Not run (the agent's reason, not checked)

- swarmai · python scripts/lint\_skills.py: no skill changed
- swarmai · python scripts/lint\_experts.py: no expert changed
- swarmai · bash scripts/sync-version.sh check: no version file changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/run\_protocol/knowledge\_counts.py
- swarmai: backend/core/swarm\_out\_live.py
- swarmai: backend/tests/test\_swarm\_out\_live.py
