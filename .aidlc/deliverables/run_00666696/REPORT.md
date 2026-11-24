# Implement design r2 option A: a shared runs store, a session-scoped RunBoard in… · Audit through DELIVER

Delivered: Implement design r2 option A: a shared runs store, a session-scoped RunBoard in a third Canvas tab, a tab marker, waitsOnPerson everywhere, and wake metadata rendered as a system line. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 51m 13s wall · 51m 13s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 1 red at base · 5/5 findings closed (5 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 878k comparable · 694k input · 184k output · 58.1M cache reads, not counted · 58.9M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · How does the shared store stay fresh for a view that mounts while the poll already runs? → subscribe loads again only when the last load is older than a 5 s window and none is in flight; reload() forces a new load whose answer wins over an older one. Why: a newly opened panel was fresh before (usePoll loaded on mount); two views mounting together still make one request (DESIGN\_SPEC, agent)
- D2 · How is the Runs view reset when the tab changes session? → the slice records the session the Runs view was opened for (runsFor); the shown tab is Artifacts when it differs from the tab's current session. Why: ChatPage's activeTabId and sessionId can change in different renders; an effect keyed on them would reset a tab during an ordinary tab switch (DESIGN\_SPEC, agent)
- D3 · Where is the answer 'does this need you' for the card, the track and the counts? → runReview.waitsOnPerson for all of them; dispatchTrack's you chip follows it; RunBoard's live/recent split moves to runReview.boardParts used by the board and the marker. Why: design invariant 2; RunBoard showed an amber card from dispatchTrack kind you even when waitsOnPerson was false (DESIGN\_SPEC, agent)
- D4 · Does an ask of a run the agent answers for count on the badge? → no: an ask whose listed run has needs\_person false (not settled) is not counted; an ask of no listed run or of a run with no live session still counts. Why: design T6: asks follow the run's judgment (DESIGN\_SPEC, agent)
- D5 · What does the wake metadata hold? → origin wake and a wake object with the kind and each dispatch's id, title and stage from this host's snapshot; merged only for system-origin turns and never over client\_id. Why: Host fields only; a person's message can never be marked as a wake (DESIGN\_SPEC, agent)
- D6 · funnelTrack test expected a stopped run with a re-run to give (no live session) to show the red stop chip → it now shows you: waitsOnPerson says it waits on the person and the board's card was already amber for it. Why: design invariant 2: one answer for the card and the track (BUILD report · swarmai, agent)
- D7 · Swarm Run children of the session's lead run → listed as their own cards like the Swarm Out board does (it has no lead grouping). Why: the design deferred to RunBoard's existing rule; the code has none (BUILD report · swarmai, agent)
- D8 · the agent's Canvas snapshot names a tab → prompt\_builder names the Runs tab instead of reading it as Artifacts. Why: proprioception must not misreport what the user sees (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep each view's own poll and add a Canvas-only poll for the session's runs. Not taken: design invariant 4 requires one poll; a fifth poll multiplies requests and lets views disagree between polls
- A3 · Detect wakes in the frontend by the marker text instead of new metadata. Not taken: identity must be decided by a producer-assigned field, never by content; a person's message starting with the marker would be hidden

Requirements analysis:

- missing\_edge (R1.1): Swarm Run children carry dispatched\_from of the lead's session; the design says they group under the lead run as RunBoard does. The filter keeps children of a run this session dispatched only when their own dispatched\_from matches, which the backend writes for children too. → rewritten
- missing\_edge (R4.1): A view mounted while the shared poll already runs would show an answer up to one interval old; the store loads again on subscribe only when its last load is older than a freshness window and none is in flight, so two views mounting together still make one request. → rewritten
- missing\_edge (R2.1): RunBoard's card treated dispatchTrack kind you as waiting on the person even when waitsOnPerson is false; the card and the track now take waitsOnPerson as the one answer. → rewritten
- missing\_edge (R6.2): Wakes persisted before this change carry no metadata; they keep rendering as before (marker text) because identity is decided by the producer's metadata, never by text. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 6/27 tool calls
- correctness · review r1: read 100% of its prompt · 19/27 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 130bb5689d44: 9 pass, 0 fail, 0 not run
- test · swarmai @ 2522f90a0d50: 12 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: pass
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npm run build → pass (bundle size within caps (main 3,780 kB of 3,832 kB))
- bash scripts/sync-version.sh check → pass
- npx vitest run --reporter=verbose → pass (484 files, 5402 tests)
- 4 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 5 failures, the same on the run's base tree: test\_silent\_except\_baseline (new silent excepts in core/carriers/ssh.py, ssh\_tunnel.py and core/swarm\_out.py, files this run does not change) and test\_precommit\_uv\_lock (uv is not on PATH on this Hive: shutil.which('uv') is None). Every other BVT test pas

## Not run (the agent's reason, not checked)

- swarmai · python -m scripts.feature\_map replay --affected --run run\_00666696: environment: all 16 reached walks not\_run, runtime unavailable on this Hive (the pinned Chromium lacks system libraries, and there is no swarm-replay user); replay on the person's machine

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_swarm\_out\_wake\_metadata.py
- swarmai: desktop/src/components/file-viewer/CanvasRuns.tsx
- swarmai: desktop/src/components/swarm-out/runsPoll.test.ts
- swarmai: desktop/src/components/swarm-out/runsPoll.ts
- swarmai: desktop/src/components/swarm-out/sessionRuns.test.tsx
- swarmai: desktop/src/components/swarm-out/sessionRuns.ts
- swarmai: desktop/src/hooks/\_\_tests\_\_/useCanvasHost.runsTab.test.ts
- swarmai: desktop/src/pages/chat/components/SessionTab.runs.test.tsx
- swarmai: desktop/src/pages/chat/components/WakeLine.tsx
- swarmai: desktop/src/pages/chat/components/\_\_tests\_\_/WakeLine.test.tsx
- swarmai: desktop/src/utils/canvasRuns.ts
