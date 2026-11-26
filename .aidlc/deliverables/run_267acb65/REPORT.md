# # Fix F: an agent can put CI or agent-configuration files on main through lande… · Audit through DELIVER

Delivered: # Fix F: an agent can put CI or agent-configuration files on main through landed-by-hand Swarm Out brings a Hive run's commits back to the owner's machine. \`backend/core/hive\_return.py\` refuses an age. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 33m 39s wall · 33m 39s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 7 not run · 1 red at base · 1/3 findings closed (5 reported; within 10 lines of one file they count once) (1 test-closed, confirmed by review) · 1 fail-to-pass · 1 HIGH finding open |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 391k comparable · 315k input · 77k output · 13.4M cache reads, not counted · 13.8M billed · $6.73 book cost · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 5 · design recalled · 7 · cited 2 · searched the DDD 1×

## Decisions

- D1 · What counts as 'a person landing by hand'? → the run card's Landed by hand button (POST manual-landed, set\_manual\_landed), unchanged. Why: the landed-by-hand route already answers 403 to the person; every verify\_hand\_landing caller is the agent (DESIGN\_SPEC, agent)
- D2 · Should land()'s 'land them by hand' answers change for the person? → no: the note that only a person lands person-only files is added for the agent only (allow\_git\_exec False). Why: the person may land them; telling the person otherwise would be wrong (DESIGN\_SPEC, agent)
- D3 · Does the in-loop check cover every person-only path or only dropped ones? → only dropped ones. Why: a not-dropped one is refused up front; covering both made the up-front check unobservable (the mutation check missed it) (BUILD report · swarmai, agent)
- D4 · Does a declared --dropped lift the person-only refusal on the hand path (the task said returned minus dropped)? → no: any returned person-only file refuses the agent's hand landing. Why: review showed the drop exemption is bypassable (the file in a commit not named, or a resolution changing one line); land() has no drop either and refuses such a run whole, so this is the same outcome as the normal refusal (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Check person\_only in swarm\_out\_settle.set\_landed\_by\_hand before calling the verifier. Not taken: the verifier already computes the whole returned set (\_changed base..tip) and the drops; a second computation in settle would duplicate it, and could not see which named commit lands a dropped file
- A3 · Check only returned minus dropped, as the task states. Not taken: an agent that cherry-picks the CI file and names it --dropped would still settle the run with the CI bytes on main: the defect reopened by one flag

Requirements analysis:

- missing\_edge (R1.2): the task subtracts declared-dropped files; without a check an agent could cherry-pick the CI file and declare it dropped, which reopens the defect → rewritten
- missing\_edge (R1.4): 'a person landing by hand' is the manual-landed button: the landed-by-hand route already refuses the person (403), so the person path is set\_manual\_landed, which is not changed → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 8/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 10/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 48bdd064e42b: 4 pass, 0 fail, 0 not run
- test · swarmai @ fd1e1f469225: 7 pass, 0 fail, 7 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- – capabilities-view-6qa4 · \* [\*] not\_run: runtime unavailable
- – chat-tab-draft-nfu9 · \* [\*] not\_run: runtime unavailable
- – api-health-q56c · The backend answers health checks [command] not\_run: runtime unavailable
- – chat-send-stream-h4kw · \* [\*] not\_run: runtime unavailable
- – chat-tool-card-m2rp · \* [\*] not\_run: runtime unavailable
- – chat-tabs-apart-x7tn · \* [\*] not\_run: runtime unavailable
- – chat-stop-p9ds · \* [\*] not\_run: runtime unavailable
- – chat-model-error-b3qz · \* [\*] not\_run: runtime unavailable
- – chat-thinking-t5wq · \* [\*] not\_run: runtime unavailable
- changed paths the targets ignore: backend/tests/test\_swarm\_out\_landed\_by\_hand.py


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_swarm\_out\_landed\_by\_hand.py -q --timeout=60 --maxfail=1000 -k person\_only → pass (the person-only hand-landing tests (fail on base))
- .venv/bin/python -m pytest tests/test\_swarm\_out\_landed\_by\_hand.py -q --timeout=60 --maxfail=1000 → pass (39 passed)
- .venv/bin/python -m pytest tests/test\_hive\_return.py tests/test\_swarm\_out\_settle.py tests/test\_swarm\_out\_actor.py -q --timeout=120 --maxfail=1000 → pre\_existing (211 passed, 1 failed: test\_swarm\_out\_settle.py::test\_cleanup\_reminder\_counts\_from\_the\_last\_change fails identically on the run's base (its 'a turn ended since' row reuses the fixed ENDED 2026-10-30, n)
- python scripts/smoke\_import.py → pass (710 modules import)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- python scripts/lint\_skills.py → pass (SKILL.md changed)
- python scripts/lint\_experts.py → pass (ok)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_267acb65 → not\_run (feature-map replay --affected: 9 reached features not run, runtime unavailable on this host (pinned Chromium lacks system libraries; no swarm-replay user))
- npx tsc --noEmit → not\_run (no desktop file changed; run through task\_checks on the landed branch)
- 5 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · .venv/bin/python -m pytest tests/test\_hive\_return.py tests/test\_swarm\_out\_settle.py tests/test\_swarm\_out\_actor.py -q --timeout=120 --maxfail=1000: 211 passed, 1 failed: test\_swarm\_out\_settle.py::test\_cleanup\_reminder\_counts\_from\_the\_last\_change fails identically on the run's base (its 'a turn ended since' row reuses the fixed ENDED 2026-10-30, now 27 days old); unrelated to this change

## Not run (the agent's reason, not checked)

- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_267acb65: feature-map replay --affected: 9 reached features not run, runtime unavailable on this host (pinned Chromium lacks system libraries; no swarm-replay user)
- swarmai · npx tsc --noEmit: no desktop file changed; run through task\_checks on the landed branch
- swarmai · npm run lint:contract: no desktop file changed; run through task\_checks on the landed branch
- swarmai · npm run build: no desktop file changed; run through task\_checks on the landed branch
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run through task\_checks on the landed branch
- swarmai · bash scripts/sync-version.sh check: run through task\_checks on the landed branch
- swarmai · bash scripts/run\_bvt.sh: run through task\_checks on the landed branch
