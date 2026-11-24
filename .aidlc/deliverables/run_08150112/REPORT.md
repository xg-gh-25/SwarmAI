# ## Goal: a run records how it actually ended, and the board shows why it is not… · Audit through DELIVER

Delivered: ## Goal: a run records how it actually ended, and the board shows why it is not settled Today \`state\_reason\` keeps only the latest value, so a run's real end is overwritten by the machine's current st. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 37m 34s wall · 37m 34s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 1 red at base · 3/4 findings closed (5 reported; within 10 lines of one file they count once) (3 test-closed, confirmed by review) · 2 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 549k comparable · 423k input · 125k output · 31.0M cache reads, not counted · 31.5M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 3 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which ending names a stopped turn's actor → the last stop or discard command in the settle record's commands at or before the result's ended\_at: by person -&gt; stopped\_by\_person, by agent or product -&gt; stopped\_by\_agent; none recorded -&gt; stopped\_by\_person (a stop given in the Hive's own page). Why: the commands list is the one record of who gave a command (DESIGN\_SPEC, agent)
- D2 · What happens to end after a re-run → an end older than the kept record's resent\_at is a previous turn's: /runs does not show it, and the next turn's end replaces it; nothing else ever replaces an end. Why: a re-run reopens the same session; showing the previous turn's end on a running run would be false (DESIGN\_SPEC, agent)
- D3 · Stage of a stopped-Hive run without a final result → stopped / why hive\_stopped (actions as any stopped run); a Needs You item is not added while a live owner session answers for it (existing owned() rule). Why: no session can run on a stopped Hive; the owner agent can start it or re-run (DESIGN\_SPEC, agent)
- D4 · Where the chat card reads its status → the backend's stage/why through stageOf and the stage words moved into a pure module (components/swarm-out/stage.ts) that takes a t function; runReview re-exports them with the app i18n. Why: the chat card must not bind the app's i18n instance at import (its own comment, and its tests mock react-i18next) (DESIGN\_SPEC, agent)
- D5 · turn\_limit is not in the request's list of end reasons → kept as its own reason turn\_limit. Why: calling it completed or crashed would be the information loss this change removes (BUILD report · swarmai, agent)
- D6 · rows dispatched before Project Agents → they get no end (annotate does not look at them). Why: they have no settle record; the 154-run export concerns Project Agent runs (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Write end inside the carriers' state() the moment they first report a stopped reason (agentcore carrier\_expired, ec2 hive\_stopped). Not taken: the first machine state observed is often not how the session ended: a completed run looked at only after 8 h or after its Hive idled off would be stamped carrier\_expired / hive\_stopped forever, which is the bug itself; and state() has no result, no commands and no settle lock
- A3 · Make state\_reason sticky (reorder agentcore \_ended\_here to read the end marker before the age check, and keep the last EC2 reason). Not taken: the person asked to keep state\_reason as the live machine state, and an EC2 run has no end marker at all; a sticky state\_reason still could not say completed vs crashed

Requirements analysis:

- missing\_edge (R1.1, R1.3): a re-run sends a new turn into the same session after an end was written; handled: an end older than the kept record's resent\_at belongs to an earlier turn: /runs does not show it and the new turn's end may be written; a machine state never replaces an end of the same turn → rewritten
- missing\_edge (R1.2): a stopped Hive or an expired Elastic carrier while the result is only not fetched yet: writing hive\_stopped/carrier\_expired then would record the machine state instead of how the session ended (the session may have completed before); handled: no end is written while a result can still come; daemon\_stopped from the Hive's own record gives hive\_stopped, result\_lost gives carrier\_expired → rewritten
- missing\_edge (R1.1): turn\_limit is an ending the request's list does not name; handled: kept as its own reason turn\_limit rather than mislabelled completed or crashed → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 16/26 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ e041df0719a9: 8 pass, 0 fail, 0 not run
- test · swarmai @ c317f8a4b541: 12 pass, 0 fail, 1 not run
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
- .venv/bin/python -m pytest tests/test\_swarm\_out\_settle.py tests/test\_hive\_run\_funnel.py -q --timeout=60 --maxfail=1000 -p no:cacheprovider → pass (109 passed; the new tests fail on the base tree)
- npx vitest run src/pages/chat/components/\_\_tests\_\_/AssistantMessageView.dispatchCard.test.tsx src/components/swarm-out/runEnd.test.tsx → pass (the chat card and end/why\_not tests pass; they fail on the base tree)
- python scripts/smoke\_import.py → pass (697 modules import)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- python scripts/lint\_skills.py → pass (clean)
- python scripts/lint\_experts.py → pass (clean)
- npx tsc --noEmit → pass (clean)
- npm run lint:contract → pass (clean)
- npm run build → pass (built; bundle under caps)
- 4 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 1363 tests; only the 3 tests/test\_precommit\_uv\_lock.py tests fail because uv is not on this machine's PATH (UV is None); they fail the same way on the base tree

## Not run (the agent's reason, not checked)

- swarmai · .venv/bin/python -m scripts.feature\_map replay --affected --run run\_08150112: environment: all 16 reached walks not\_run, runtime unavailable (the pinned Chromium lacks system libraries and there is no swarm-replay user on this machine); the installed SwarmAI's feature\_map refuses features.yaml schema 2

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: desktop/src/components/swarm-out/runEnd.test.tsx
- swarmai: desktop/src/components/swarm-out/stage.ts
