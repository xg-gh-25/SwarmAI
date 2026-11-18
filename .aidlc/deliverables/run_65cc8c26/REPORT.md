# Implement the Swarm Out run card redesign and the defects it found (XG: "把问题还有最… · Audit through DELIVER

Delivered: Implement the Swarm Out run card redesign and the defects it found (XG: "把问题还有最新的design 都实现"; mockup Knowledge/Designs/2026-11-18-swarm-out-run-card-redesign-mockup.html, three states: running native,. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 56m 14s wall · 56m 14s active · 36.8% budget |
| Quality | WARN | 1/1 verification · 1 not run · 2 red at base · 4/4 findings closed (1 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 441k comparable · 338k input · 103k output · 50.4M cache reads, not counted · 50.9M billed · $14.84 book cost · host-session responses in the run window · 10.8% budget |

Project Agent: SwarmAI · code-change · 11 checks · 0 rules · 2 watches

Knowledge: requirement recalled · 10 · design recalled · 7 · cited 1 (+2 found on demand) · searched the DDD 1×

## Decisions

- D1 · Where do the gates' who/when go? → FunnelLine moves from under the title to directly under the dispatch track, unchanged. Why: one representation of the funnel, next to the track it details; its tests stay (DESIGN\_SPEC, agent)
- D2 · What is the order of the main column? → track + funnel + now line, then the wait and ask-person cards, then Your ask, then Done when with the machine checks (testid swarm-out-review-acceptance kept), then the tabs. Why: progress and what waits on the person first; the criteria are the reference, the detail recedes; the mockup's verdict sentence is not built because no backend field states it, the rail's state sentence carries what is needed (DESIGN\_SPEC, agent)
- D3 · Which tone token for running? → a new --color-status-running (#2b6cee light, #4c8dff dark) that no accent rule overrides; TONE\_VAR.busy reads it. Why: every surface reading TONE\_VAR.busy is fixed at once; amber stays only for the person (DESIGN\_SPEC, agent)
- D4 · What does the live read take for 'now'? → the newest assistant message's newest text block's last non-empty line and its newest tool\_use block's display tool and object (the stored redacted copy), control characters removed, capped at 240/40/160. Why: the display copy is already redacted for the chat; thinking blocks are not the agent's words to the person (DESIGN\_SPEC, agent)
- D5 · How is the acceptance split? → on semicolons, trimmed, empty pieces dropped, each clause unchanged. Why: the contract holds one string today; a list in the contract is a later change (DESIGN\_SPEC, agent)
- D6 · Should stop move below open session when it is the backend's primary? → no: the order stays as the backend gives it (primary first, a tested contract); stop is demoted by style (outlined) instead. Why: the panel renders the backend's actions as given (BUILD report · swarmai, agent)
- D7 · Where does the time go? → the status pill names the stage only; the rail's Now line keeps the stage with its why and how long. Why: one representation of the time (BUILD report · swarmai, agent)
- D8 · The mockup's one-sentence verdict once back → not built; the rail's state sentence and the machine checks carry it. Why: no backend field states that verdict; composing it here would be a guess (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Rewrite RunOverlay as a new component and switch callers. Not taken: 927 lines of tested behaviour (keys, J/K, sheets, Esc layering, Canvas diff, waits) would be re-done for a layout change; strangler-fig is not needed when the behaviour stays
- A3 · Read 'what it does now' with a second Hive call per run. Not taken: the live read already fetches the newest turn of the same session; a second call doubles Hive traffic for the same bytes

Requirements analysis:

- missing\_edge (R2.3): the Hive's turn is external content: only three fields, type- and length-checked, and React text rendering (no markdown) on the card → rewritten
- missing\_edge (R2.1): the tool object is the stored redacted display copy (display.object), never the raw input → rewritten
- missing\_edge (R3.6): a running run had five tabs of which three were empty → rewritten
- missing\_edge (R1.2): PipelineLine, RunList, HistoryView, LiveDispatches and PipelineOverlay read TONE\_VAR.busy; fixing the token fixes them all → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 6/26 tool calls
- correctness · review r1: read 100% of its prompt · 10/26 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 1/25 tool calls
- correctness · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r2: read 100% of its prompt · 2/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ fc54d8efbce7: 9 pass, 0 fail, 0 not run
- test · swarmai @ 99a691e4741d: 8 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pre_existing
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not run
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- npx vitest run src/components/swarm-out/runCard.test.tsx → pass (9 passed on this tree, after the header comment edit)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_live.py tests/test\_pipeline\_stage\_events.py -q --timeout=60 → pass (66 passed (backend code unchanged since BUILD but for the module doc line))
- npx vitest run src/components/swarm-out src/components/layout/PipelineOverlay.test.tsx src/components/layout/pipelineRows.test.ts → pass (23 files, 256 passed: the card tests and every reader of TONE\_VAR.busy and the run card)
- npx tsc --noEmit → pass (clean)
- npm run lint:contract → pass (clean)
- npm run build → pass (built; bundle 4,040 kB under the 4,186 kB cap)
- bash scripts/run\_bvt.sh → pre\_existing (907 passed, 2 failed, both red on the run's base the same way: test\_frontend\_design\_web\_guidance (a vendored guide missing from its provenance file, landed by another session) and test\_eval\_fresh\_inst)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass (648 modules imported)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (clean)
- /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pre\_existing (the same 214 findings as on the run's base (hashes in the vendored modern-web-guidance files); none in this run's files)
- 1 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main 17109f487760-&gt;739735668ce1): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 907 passed, 2 failed, both red on the run's base the same way: test\_frontend\_design\_web\_guidance (a vendored guide missing from its provenance file, landed by another session) and test\_eval\_fresh\_install (a CI\_IMPACT probe timing out at 20 s on a loaded machine); 55 manifest files in three parts wit
- swarmai · /Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin/python scripts/security\_scan.py: the same 214 findings as on the run's base (hashes in the vendored modern-web-guidance files); none in this run's files

## Not run (the agent's reason, not checked)

- swarmai · npx vitest run --reporter=verbose: the run's readers ran above (every Swarm Out test and the Pipelines views that read TONE\_VAR.busy); the whole suite is CI's on a loaded machine

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: desktop/src/components/swarm-out/runCard.test.tsx
- swarmai: desktop/src/components/swarm-out/runCard.tsx
- swarmai: desktop/src/components/swarm-out/runCardRules.ts
