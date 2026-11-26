# # Fix D: after upgrading to 2.6.0, a pipeline run started on 2.5.5 is stuck (ev… · Audit through DELIVER

Delivered: # Fix D: after upgrading to 2.6.0, a pipeline run started on 2.5.5 is stuck (every command fails) Pipeline v1 lives in \`backend/core/run\_protocol/\`. Every read of a run replays its event log. \`lifecyc. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 48m 24s wall · 48m 24s active · no baseline yet (3/5 runs) |
| Quality | WARN | 1/1 verification · 1/4 findings closed (5 reported; within 10 lines of one file they count once) · 1 fail-to-pass · review coverage: correctness r1 read 56% of its prompt |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 590k comparable · 473k input · 116k output · 24.8M cache reads, not counted · 25.4M billed · $11.08 book cost · host-session responses in the run window · no baseline yet (3/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 1 (+4 found on demand) · searched the DDD 2×

## Decisions

- D1 · Which rosters does an unstamped attempt accept? → The rosters of every released version that wrote no stamp: version 1 (v2.4.0-v2.5.5) and version 2 (v2.6.0). Why: Measured from git: computing stage\_checks at every commit of checks.py/lifecycle.py shows one roster from 2026-10-27 until FM-1 (2026-11-19); v2.4.0 (2026-11-01) is the first Pipeline v1 release. An unstamped attempt cannot say which of the two wrote it (a run created on 2.5.5 can reach TEST on 2.6.0), so either is accepted; dev-only rosters older than v2.4.0 are not included (DESIGN\_SPEC, agent)
- D2 · Which attempts are stamped? → Every attempt complete\_stage writes except DELIVER; land attempts are written elsewhere and stay unstamped. Why: DELIVER and land attempts are validated against lifecycle's fixed sets, not the registry; a stamp there would mean nothing. The guard test freezes those sets so a change to them fails CI until it is versioned (DESIGN\_SPEC, agent)
- D3 · How is the history stored? → Per version, only the rosters that changed to the next version (\_ROSTER\_CHANGES\[v\]); roster\_at walks forward to the first change after v, else today's registry. Why: Compact, and a check removed later needs only its id in the history, never its evaluator (DESIGN\_SPEC, agent)
- D4 · How is the 2.5.5 log made? → Recorded once from the v2.5.5 release's own code (git archive ad726dd41) with the repo's rig and a fixed commit date, committed as a fixture with its generator. Why: A log synthesized by today's code would only imitate 2.5.5; the fixture is what an upgraded install really has on disk. The test rebuilds the same base commit and asserts it, so a drift in rig files is reported, not silently mis-tested (BUILD report · swarmai, agent)
- D5 · What about landing tests that fail without a git identity on this host? → The upgrade test sets the rig's identity (t / t@example.invalid) in its environment; the existing e2e tests were run with it set. Why: The failures are 'git has no user.name / user.email configured' from the machine, not the change (BUILD report · swarmai, agent)
- D6 · Secret-scan false positive in the fixture: baseline or source? → Leave run.json's projection\_of out of the fixture (generator updated, fixture regenerated). Why: It is a cache marker the reader rebuilds; changing the security baselines would also rewrite unrelated line numbers in them (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Record the run's stage rosters in its protected contract.json at create and pin every attempt of the run to them. Not taken: A run then never gets a check added later (FM-1 would not guard runs created before it), every reader of the current roster (next\_step.blocking\_checks, engine stage-complete) would need the contract's roster, and a check removed from the registry would leave old runs unable to evaluate it: the same stranding, moved to the writer. Legacy contracts would still need the frozen legacy rosters.
- A3 · Accept {TS-1, TS-4} as an extra legal TEST set in lifecycle.\_attempt. Not taken: Fixes only this upgrade; the next roster change strands runs again, which the requirement forbids.
- A4 · Migrate old event logs on read (rewrite the attempt's check\_ids). Not taken: The log is hash-chained and append-only; rewriting history is destructive and hides what was actually checked.

Requirements analysis:

- missing\_edge (R1.1): An unstamped attempt cannot say which version wrote it: a run created on 2.5.5 may have reached TEST on 2.6.0. Unstamped attempts are accepted against any roster a pre-stamp released version used (v2.4.0-v2.5.5 and v2.6.0, measured from git history: every release since v2.4.0 used one roster until FM-1). Dev-only rosters from before v2.4.0 are not included. → rewritten
- missing\_edge (R2.2): A check removed from the registry in a future version must not break old attempts: the history stores check ids only, so validation never needs the removed check's evaluator. → rewritten
- vague (R1.2): 'continued' is read as: the next command the run's next step names (DELIVER land) completes on HEAD. → rewritten
- missing\_edge (R2.4): Land and DELIVER attempts are validated against fixed sets in lifecycle.py (NORMAL\_LAND\_CHECKS, LG\_CHECKS, SKIP\_LAND\_CHECKS, RF\_CHECKS, REPORT\_ONLY), not the registry. They never changed since Pipeline v1 began; versioning them is out of scope, but the guard test freezes them so a future change fails CI and must version them before it lands. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 56% of its prompt · 13/26 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/26 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 93a6d5a43f17: 5 pass, 0 fail, 0 not run
- test · swarmai @ c219b04ab98a: 7 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: pass
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/4/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7ac7f6eb/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- changed paths the targets ignore: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/00a4e35790cab992ec3bea72642d2db752c7c590eec2b4dcb8a7f58ba44b3759, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/2b82a79f8d8835059c7be3291c5b267c59a71b2c8875cbb6b3014dc1b8784b3a, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/63faa11c016d351fd359f8d6923d3119c504b97bb8a70516703350247a51de02, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/8dbaee226e316ebe997f329d4e4f9cbdbcbfc8fc99fd10c87b2ccb09e091047d, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/dc10d0fa8152ee3794959e6edbf4777e669d1adccc0b71f03dcb764d57d0448d, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/code\_graph\_callers/43648b210baaf49b.json, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/contract.json, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/events.jsonl, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/integration\_branches.json, backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/sessions/aaaaaaaa-0000-4000-8000-000000000001/events.jsonl


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_pipeline\_v1\_roster\_versions.py tests/test\_pipeline\_v1\_upgrade\_replay.py -q --timeout=180 --maxfail=1000 -p no:cacheprovider → pass
- .venv/bin/python -m pytest tests/test\_run\_protocol\_lifecycle.py tests/test\_pipeline\_v1\_upgrade\_replay.py -q --timeout=250 --maxfail=1000 -p no:cacheprovider → pass
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- bash scripts/sync-version.sh check → pass
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_7ac7f6eb → pass

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/00a4e35790cab992ec3bea72642d2db752c7c590eec2b4dcb8a7f58ba44b3759
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/2b82a79f8d8835059c7be3291c5b267c59a71b2c8875cbb6b3014dc1b8784b3a
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/63faa11c016d351fd359f8d6923d3119c504b97bb8a70516703350247a51de02
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/8dbaee226e316ebe997f329d4e4f9cbdbcbfc8fc99fd10c87b2ccb09e091047d
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/blobs/dc10d0fa8152ee3794959e6edbf4777e669d1adccc0b71f03dcb764d57d0448d
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/code\_graph\_callers/43648b210baaf49b.json
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/contract.json
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/events.jsonl
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/runs/run\_a134e90d/integration\_branches.json
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/sessions/aaaaaaaa-0000-4000-8000-000000000001/events.jsonl
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/audit/sessions/aaaaaaaa-0000-4000-8000-000000000001/runs.json
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/fixture.json
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/workspace\_run/DESIGN\_SPEC.md
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/workspace\_run/REQUIREMENTS.md
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/workspace\_run/REVIEW.md
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/workspace\_run/artifacts/design\_spec.json
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/workspace\_run/artifacts/knowledge.md
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/workspace\_run/artifacts/knowledge\_design.md
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/workspace\_run/artifacts/requirements.json
- swarmai: backend/tests/fixtures/pipeline\_v1\_v2\_5\_5\_run/workspace\_run/evidence/prompts/correctness-r1-a1.md
- swarmai: 5 more in run_audit.json
