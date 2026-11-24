# Close seven Swarm Out dead ends so the owner agent always has a command that mo… · Audit through DELIVER

Delivered: Close seven Swarm Out dead ends so the owner agent always has a command that moves a run on, and only waits the person alone can answer reach Needs You. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 50m 32s wall · 50m 32s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 2 not run · 3 red at base · 6/6 findings closed (10 reported; within 10 lines of one file they count once) (5 test-closed, confirmed by review) · CF-a41fae94: no mutation check (a test-only change: the test's expected text follows the message this run changed) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.1M comparable · 903k input · 237k output · 66.4M cache reads, not counted · 67.5M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 2 (+3 found on demand) · searched the DDD 2×

## Decisions

- D1 · How does an Elastic round that ended early become discardable? → writes\_end\_at returns the time this host recorded a session-written end (closed / ended / carrier\_replaced) when earlier than the lifetime bound; the existing round\_end / result\_lost rule then marks it once a drained pull started after it. Why: reuses the one proof the Host already trusts (a whole read after the round could write nothing more) instead of a second rule (DESIGN\_SPEC, agent)
- D2 · Where is adoption kept? → one small file state/swarm-out/owners.json (dispatch -&gt; session, by, at), read by swarm\_run.owner\_of through a stat-keyed cache, plus a decision entry in the run's record. Why: owner\_of is the single authority every reader uses; one file reaches every caller without attaching a field to each row producer (DESIGN\_SPEC, agent)
- D3 · What is the same person? → sessions.user\_id of the adopting session equals the run's dispatched\_person (recorded at dispatch when the session has a user id; absent means the install's owner). Why: the owner session row is gone at adoption time, so the person must be recorded when the run is sent (DESIGN\_SPEC, agent)
- D4 · May the host answer in free text? → no: the host still picks options (the Hive's bridge enforces it); a truncated approval can only be denied; a wait the host cannot answer at all goes to Needs You. Why: keeps the existing security boundary (Hive-written text never chooses) while removing the chat-card shape limits (DESIGN\_SPEC, agent)
- D5 · Re-wake bounds → REWAKE\_AFTER\_S = 3 h unchanged, MAX\_REWAKES = 3 per unchanged state; then one Needs You item per run; a new state resets the counter. Why: a few hours lets the agent finish other work; a counter, not a timer, bounds it (DESIGN\_SPEC, agent)
- D6 · Does host\_answerable refuse waits with missing fields? → only on positive evidence the wait shows (omitted questions, a question without options, a truncated decision). Why: the Hive lists every wait with its kind and fields; an answer the Hive refuses still errors to the agent, which then escalates (BUILD report · swarmai, agent)
- D7 · Is an accepted run held only by files actionable for its owner? → yes: it wakes the owner, who waits or settles without them. Why: otherwise nothing ever prompts the agent and the run hangs to expiry, the reported case (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Let the agent discard or settle any stuck run outright (drop the discard\_blocker checks for stopped/accepted runs). Not taken: loses commits and files that exist only on the Hive with no recorded decision; the person asked for recorded agent decisions, not a weaker guard
- A3 · Hand stuck runs to the person after a timer (Needs You for every dead end). Not taken: the person's rule: default stand-by, the agent operates; do not abuse Needs You. Only what only the person can answer goes there

Requirements analysis:

- missing\_edge (R4.1): the owner session row is deleted, so its person cannot be read at adoption time: the dispatching person is recorded on the dispatch row from now on; a row without it belongs to the install's owner (no user id) → rewritten
- missing\_edge (R5.1): the host still answers only with options, never free text, and a truncated approval can only be denied, as the Hive's bridge enforces → rewritten
- missing\_edge (R7.1): a re-wake carries no new digest, so its message names the runs (Host fields only) → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 14/27 tool calls
- correctness · review r1: read 100% of its prompt · 23/27 tool calls
- security · review r2: read 100% of its prompt · 14/25 tool calls
- correctness · review r2: read 100% of its prompt · 17/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 13/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 93bb6f474d39: 10 pass, 0 fail, 0 not run
- test · swarmai @ 7c19b4ba5aab: 15 pass, 0 fail, 2 not run
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
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_dead\_ends.py tests/test\_swarm\_out\_actor.py tests/test\_swarm\_out\_settle.py  → pass (533 tests on the reviewed tree; every fix mutation-checked (31 caught, 0 missed))
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_dead\_ends.py tests/test\_swarm\_out\_actor.py tests/test\_swarm\_out\_settle.py  → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_attention\_authority.py tests/test\_carriers\_agentcore.py tests/test\_swarm\_run\_childre → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_card.py tests/test\_swarm\_out\_e2e\_fixes.py tests/test\_swarm\_out\_e2e\_round2. → pass (the other tests importing the changed modules)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_hive\_boot\_close\_turns.py::test\_lifespan\_closes\_turns\_before\_claims\_and\_bridge → pre\_existing (a source-shape assertion on main.py's lifespan: fails the same way on the base tree)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_waits.py::test\_list\_runs\_splits\_what\_needs\_the\_person\_from\_what\_was\_answer → pre\_existing (KeyError 'waiting': fails the same way on the base tree)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_chat\_scenarios\_e2e.py -n 0 → pass (chat-brain-check quick tier)
- npx vitest run src/services/attention.test.ts src/pages/chat/swarmOutFresh.test.ts src/pages/chat/components/WelcomeScreen.test.tsx src/components/swarm-out/swarmOutWelcome.test.tsx src/components/lay → pass
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- 10 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_hive\_boot\_close\_turns.py::test\_lifespan\_closes\_turns\_before\_claims\_and\_bridge: a source-shape assertion on main.py's lifespan: fails the same way on the base tree
- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_waits.py::test\_list\_runs\_splits\_what\_needs\_the\_person\_from\_what\_was\_answer: KeyError 'waiting': fails the same way on the base tree
- swarmai · bash scripts/run\_bvt.sh: only tests/test\_precommit\_uv\_lock.py fails (TypeError: a None path; same on the base tree, this machine's environment); the other 80 BVT files: 1360 passed with --maxfail=1000

## Not run (the agent's reason, not checked)

- swarmai · npx vitest run --reporter=verbose: the whole frontend suite is the CI check; it is run through task\_checks on the landed branch before the commit is reported
- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_33eb9758abd3/repos/swarmai/backend/.venv/bin/python -m scripts.feature\_map replay --affected --run run\_33eb9758: 16 walks not\_run, environment: the pinned Chromium lacks system libraries and this machine has no swarm-replay user

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_swarm\_out\_dead\_ends.py
- swarmai: desktop/src/pages/chat/swarmOutFresh.test.ts
- swarmai: desktop/src/services/attention.test.ts
