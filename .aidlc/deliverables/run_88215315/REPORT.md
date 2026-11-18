# XG: "参考下 我们最近在定义的Autonomous factory line 的visulaizaiton 方案 我们是不是优化下 我们的Pipeline… · Audit through DELIVER

Delivered: XG: "参考下 我们最近在定义的Autonomous factory line 的visulaizaiton 方案 我们是不是优化下 我们的Pipeline Overlay, Swarm out dashboard 页面啊" → after the mockup Knowledge/Designs/2026-11-18-swarmai-factory-line-overlays-mockup.h. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 41m 30s wall · 41m 30s active · 27.2% budget |
| Quality | WARN | 1/1 verification · 5 not run · 4/4 findings closed (4 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 701k comparable · 541k input · 160k output · 58.3M cache reads, not counted · 59.0M billed · host-session responses in the run window · 17.2% budget |

Project Agent: SwarmAI · code-change · 11 checks · 0 rules · 2 watches

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 5 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which time counts as waiting on a person in a stage? → Only the time the run's status is awaiting\_human (a decision is open). paused (user stop, session lost) is neither agent time nor waiting: it is left out of both. Why: awaiting\_human is the one state the product defines as the person owing an answer; a stopped run is not waiting on a decision (DESIGN\_SPEC, agent)
- D2 · Where does a Hive pipeline run sit on the Line while it is on the Hive? → Its chip spans the eight pipeline stations as one band 'on &lt;Hive&gt;' with its state; once its funnel has a gate it sits at RETURN, LAND or JUDGE. Why: The Hive does not report the stage of a run while it runs (SwarmOutResult.pipeline has run id, profile, status, landing); placing it at a stage would be a guess (DESIGN\_SPEC, agent)
- D3 · How are Swarm Run children grouped? → On the Line: Hive rows whose parent\_run equals a local row's run id sit under that local run as its work packages. In Swarm Out: within each section, children of one lead are adjacent under a one-line 'Swarm Run' header with the lead's title when the cached analytics rows hold it. Why: parent\_run is the lead pipeline run's id; the local analytics rows already carry that run and its title, so no new read is added (DESIGN\_SPEC, agent)
- D4 · What happens to the Runs tab and its Waiting / Running / Done sections? → The Line replaces the Runs tab as the default: the Waiting-on-you strip replaces 'Waiting for you', the tracks replace 'Running', and the folded Done list stays as it is. Tabs: Line, Eval. Why: This supersedes the recorded decision 'Pipelines overlay: Runs and Metrics tabs' (cb630c14) at the person's request; every row action the Runs list had stays reachable (R3.7) (DESIGN\_SPEC, agent)
- D5 · Which periods does Eval compare? → The last 15 days with the 15 before, inside the 30-day analytics window. Why: The route loads 30 days; widening it is outside this change and slows the overlay (DESIGN\_SPEC, agent)
- D6 · How is the autonomy interval computed without changing the autonomy computation? → A 95% Wilson interval in the frontend from the k and n the existing autonomy() function returns. Why: It is arithmetic on the displayed counts; which runs count stays the parallel run's (R6.5) (DESIGN\_SPEC, agent)
- D7 · Hive slots in Swarm Out → The existing Hive rows (name, version, memory, sessions used, full/busy) stay as they are: they already show load without a fixed slot count. Why: A Hive has no slot cap (live.ts capacity); drawing dots would invent one (BUILD report · swarmai, agent)
- D8 · Swarm Run header text in Swarm Out → 'Swarm Run · N work packages' counting every package of the run in the list, without the lead's title. Why: The lead title would need another read (the analytics query) in Swarm Out; the count already groups and the card names the lead (BUILD report · swarmai, agent)
- D9 · A Hive pipeline row the dispatching agent holds (needs\_person false) → fromHive follows Swarm Out's rule: it is running, not waiting on you. Why: The visual check showed the strip and the Line disagreeing about the same run (BUILD report · swarmai, agent)
- D10 · Old measures with no reader left → Deleted with their tests and locale keys. Why: Nothing reads them after Metrics became Eval; keeping them is dead code (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Write stage\_entered\_at at every site in lifecycle.py that changes run\['stage'\] or opens a stage. Not taken: About a dozen sites change the stage (\_open\_next, review reopen, deliver reopen, restore, gate0 exhaustion); patching each changes the state machine and misses any future site, and R1.3 keeps the fold unchanged. Replaying the events beside the fold sees every change by construction.
- A3 · Read the stage timeline in the analytics route from events.jsonl on every request. Not taken: The route already reads only run.json for 30 days of runs; reading and verifying each event log per request is slow on the overlay's open path, and the projection is the place read models live (TECH pitfall 3e0078ea: facts written beside the projection are lost; facts derived inside it are kept).
- A4 · Keep the Swarm Out run view as it is and draw the funnel only in the big card. Not taken: The mockup's point is that the list itself shows where each run stands; the funnel already exists as data, so a row track costs one small component and keeps the recorded row design (sections by what the person must do, one row per run).

Requirements analysis:

- missing\_edge (R4.1, R7.1): The mockup drew fixed slot counts (2 on this Mac, 4 and 2 on Hives). The code has none: a Hive queues only under memory pressure (desktop live.ts capacity) and this machine has no fixed run cap. → rewritten
- missing\_edge (R6.3): The S7 standard compares 4 weeks with the 4 before; the analytics route loads a 30-day window only. → rewritten
- missing\_edge (R1.2, R5.2): Runs projected before this change carry no stage timeline; their timing is left out, not estimated. → rewritten
- missing\_edge (R4.2): The mockup put a decision's options on the strip. The commands live on the run's big card (one home per command, fetched per run from /view); the strip opens the card. → rewritten
- missing\_edge (R1.3, R6.5): The parallel run run\_b0e2c7a4 edits autonomy, interventions and usage (including Engine.\_read\_model's usage part). This run adds its timeline as a separate helper beside it and reads the summary fields unchanged, so the two meet only on landing. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 7/27 tool calls
- correctness · review r1: read 100% of its prompt · 17/27 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ d7e8aa3dddbd: 9 pass, 0 fail, 0 not run
- test · swarmai @ 074f1b7dac56: 8 pass, 0 fail, 5 not run
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
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest tests/test\_stage\_timeline.py tests/test\_pipeline\_v1\_api.py tests/test\_pipeline\_needs\_you.py tests/test\_run\_window.py t → pass (74 passed: the plan's X1 and the existing tests of the analytics route, needs-you, run window, projection and lifecycle)
- npx vitest run src/components/layout/pipelineRows.test.ts src/components/layout/PipelineOverlay.test.tsx src/components/layout/\_\_tests\_\_/PipelineOverlay.loaderror.test.tsx src/components/swarm-out/fun → pass (13 files, 202 tests: the plan's X2-X5 and every component reading the changed rows, RunList and locales)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass (all modules import)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (clean)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pass (PASS: no new HIGH findings, secrets or wildcard CORS)
- npx tsc --noEmit → pass (clean)
- npm run lint:contract → pass (clean)
- npm run build → pass (built; bundle under its cap)
- npx vitest run --reporter=verbose → not\_run (the whole frontend suite: the scoped set above covers every reader of what changed; the full suite is CI's (AGENT R9))
- bash scripts/run\_bvt.sh → not\_run (the CI allowlist suite; the backend tests of every changed module ran above, CI runs the BVT)
- 3 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx vitest run --reporter=verbose: the whole frontend suite: the scoped set above covers every reader of what changed; the full suite is CI's (AGENT R9)
- swarmai · bash scripts/run\_bvt.sh: the CI allowlist suite; the backend tests of every changed module ran above, CI runs the BVT
- swarmai · python scripts/lint\_skills.py: no skill changed
- swarmai · python scripts/lint\_experts.py: no expert changed
- swarmai · bash scripts/sync-version.sh check: no version file changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/run\_protocol/timeline.py
- swarmai: backend/tests/test\_stage\_timeline.py
- swarmai: desktop/src/components/layout/PipelineEval.tsx
- swarmai: desktop/src/components/layout/PipelineLine.tsx
- swarmai: desktop/src/components/swarm-out/funnelTrack.test.tsx
- swarmai: desktop/src/components/swarm-out/track.ts
