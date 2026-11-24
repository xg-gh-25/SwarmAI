# Split a Swarm Out run's single minutes number into work time (per-round spans, … · Audit through DELIVER

Delivered: Split a Swarm Out run's single minutes number into work time (per-round spans, partial when a turn has no duration) and waiting time (on the Hive, the agent or the person, since final\_at), in the back. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 26m 55s wall · 26m 55s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 3 not run · 4/5 findings closed (3 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 530k comparable · 420k input · 110k output · 19.5M cache reads, not counted · 20.0M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 2 (+2 found on demand) · searched the DDD 1×

## Decisions

- D1 · What counts as the start of a duration-less outcome's time → the previous outcome of the same turn, else the turn's user row. Why: a continuation adds outcomes to one turn; counting each from the turn start would count time twice (DESIGN\_SPEC, agent)
- D2 · Where final\_at lives → record top level, final\_at plus final\_of (the ended\_at it belongs to), set in save. Why: save is the one write point and already reads the file on disk for resent\_at (DESIGN\_SPEC, agent)
- D3 · Running time of a re-run → from the open turn span's start, else sent\_at, in both where.runTime and follow. Why: a re-run 18 h later must not read 18 h running (DESIGN\_SPEC, agent)
- D4 · Round count shown → result.turns (count of turn starts), shown when &gt;1 on settled and waiting cards. Why: turn\_spans in /runs is capped; turns is the uncapped count (DESIGN\_SPEC, agent)
- D5 · Who a waiting result waits on in the UI → dispatchTrack kind judge -&gt; agent, else person; needs\_answer: track kind you -&gt; person else agent. Why: reuse the existing dispatch track, the task says so (DESIGN\_SPEC, agent)
- D6 · Round count source → result.turns, shown on waiting and settled rows when &gt; 1. Why: uncapped; showing it while a result waits too tells a re-run apart (BUILD report · swarmai, agent)
- D7 · Who a stopped run waits on in the UI → person when the track chip is 'you' or 'stop', else agent. Why: a stop nobody takes on is the person's to re-run or discard (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Frontend only: label the existing number by state (running / open / settled) without new backend fields. Not taken: cannot tell work from the gap between rounds, cannot count stopped turns, and has no moment the result became final, so 'waiting on agent' would still count from ended\_at of the last round
- A3 · Store final\_at inside result in summarize. Not taken: summarize is pure and re-run on every fetch; it cannot know when a result first became final, and \_fetch rebuilds the record from a key list, so only the write point sees the previous value

Requirements analysis:

- missing\_edge (R1.1): a turn with no outcome yet (running, or killed with no record) has no end: its span has end null and active\_s null, counted as a round but not as work → rewritten
- missing\_edge (R1.2): a turn with several outcomes (continuations): an outcome without duration counts from the previous outcome of the same turn, not the turn's start, so time is not counted twice → rewritten
- missing\_edge (R1.3, R1.4): \_fetch rebuilds the record from a key list, so final\_at is carried from the file on disk inside save, the one write point; a result that is not final with a new ended\_at clears it → rewritten
- missing\_edge (R3.1, R2.3): a re-run's running time counts from the open round's start, not the first dispatch, so a re-run 18 h later does not read 18 h of running → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 6/26 tool calls
- correctness · review r1: read 100% of its prompt · 14/26 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 8363630d8948: 8 pass, 0 fail, 0 not run
- test · swarmai @ f5111f940dc2: 8 pass, 0 fail, 3 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npm run build → pass
- .venv/bin/python -m pytest tests/test\_swarm\_out\_results.py tests/test\_swarm\_out\_follow.py tests/test\_hive\_records.py tests/test\_swarm\_out\_e2e\_round2.py tests/test\_swarm\_out\_e2e\_fixes.py tests/test\_swa → pass
- npx vitest run src/components/swarm-out/where.test.ts src/components/swarm-out/runTime.test.tsx src/components/swarm-out/runCard.test.tsx src/components/swarm-out/history.test.tsx src/components/swarm → pass
- python -m scripts.feature\_map replay --affected --run run\_67581233 → not\_run (environment: this Hive has no Chromium system libraries and no swarm-replay user (16 walks not\_run, runtime unavailable); the installed SwarmAI reads features.yaml schema 1 and the repo's is 2, so the)
- bash scripts/run\_bvt.sh → not\_run (run after landing through task\_checks on the task's checkout (backend-bvt), with the remaining Brain checks)
- 1 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · python -m scripts.feature\_map replay --affected --run run\_67581233: environment: this Hive has no Chromium system libraries and no swarm-replay user (16 walks not\_run, runtime unavailable); the installed SwarmAI reads features.yaml schema 1 and the repo's is 2, so the worktree's own feature\_map was used
- swarmai · bash scripts/run\_bvt.sh: run after landing through task\_checks on the task's checkout (backend-bvt), with the remaining Brain checks
- swarmai · npx vitest run --reporter=verbose: the whole frontend suite runs after landing through task\_checks (frontend-run-frontend-tests); here the touched files and their direct readers ran

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: desktop/src/components/swarm-out/runTime.test.tsx
