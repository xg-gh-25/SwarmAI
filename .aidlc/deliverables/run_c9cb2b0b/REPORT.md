# When this machine's branch was rewritten after a dispatch, hive\_return maps eac… · Audit through DELIVER

Delivered: When this machine's branch was rewritten after a dispatch, hive\_return maps each missing bundle prerequisite to its single patch-id equivalent on the branch and replays the bundle onto it from objects. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 33m 23s wall · 33m 23s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 7 not run · 5/7 findings closed (4 test-closed, confirmed by review) · 1 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 624k comparable · 462k input · 162k output · 22.4M cache reads, not counted · 23.1M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · how is a commit's equivalent identified → git log -p --no-merges --no-renames --binary piped through git patch-id --stable, the same command for the prerequisite and the window; several hits narrowed by author name, email and subject. Why: the same command on both sides makes the ids comparable whatever the user's diff config; the task names patch-id with author/subject as the tiebreak (DESIGN\_SPEC, agent)
- D2 · what if a mapped bundle holds a merge → refused, naming the takeover by hand. Why: a linear replay cannot place a merge; with no merge there is exactly one prerequisite, so the mapping is unambiguous (DESIGN\_SPEC, agent)
- D3 · what does import\_unrecorded do with a mapped prerequisite → accepts it and fetches the Hive's commits as they are (no replay). Why: its commits are the next round's material, never shown or landed; refusing would leave an Elastic Workers re-run stuck the same way (DESIGN\_SPEC, agent)
- D4 · how does the person's card say the base was mapped → ReturnPanel shows one line from a new locale key appended at the end of en.json and zh.json; the landed commit's message names the mapping too. Why: the task asks the card to say so; the rules allow new keys only at the end of each locale file (DESIGN\_SPEC, agent)
- D5 · when is refs/hive/&lt;dispatch&gt;/&lt;key&gt; moved to the replayed tip → right after a successful replay, before the review checks. Why: a re-prepare of an already landed bundle then leaves the ref at the landed tip (commits\_here keeps holding); a conflict keeps the Hive's own tip for the takeover (BUILD report · swarmai, agent)
- D6 · what does a tiebreak that leaves several candidates report → the candidates with the prerequisite's author and subject (all hits when none has them). Why: names the commits that are actually ambiguous (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · git rebase --onto in a scratch worktree. Not taken: writes the Hive's files to disk and runs checkout machinery (filters, hooks guarded only by config) where the objects-only path land already uses needs none; rebase also drops empty commits and re-stamps committer dates, so the same bundle would replay to new shas each time and a later turn's landed-tip ancestry check would fail
- A3 · keep refs/hive/&lt;dispatch&gt;/&lt;key&gt; at the Hive's tip and store the replayed tip under a second ref. Not taken: commits\_here (swarm\_out\_actor), the next-round start (carriers/agentcore \_returned\_starts) and land all pair state\['ref'\] with state\['tip'\]; each would need changing, and swarm\_out\_actor sits next to the settle code another worker is changing

Requirements analysis:

- missing\_edge (R1.3): a later turn after a replayed landing: the next bundle is built on the same old base, so its replay must give the same commits for the landed-tip ancestry check to pass -- handled: the replay copies each commit's author, committer, dates and message, so the same input gives the same shas; a landed Hive tip that was replayed this time is translated through the replay's commit map → rewritten
- missing\_edge (R3.2): several prerequisites come only from merges in the Hive's history; a linear replay cannot place them -- handled: a mapped bundle with a merge is refused → rewritten
- missing\_edge (R1.1): import\_unrecorded (Elastic Workers re-run) also calls \_verify\_and\_fetch; its commits are material for the next round, never shown or landed -- handled: it accepts a mapped prerequisite and fetches the Hive's commits as they are, without replay → rewritten
- missing\_edge (R3.1, R3.2): Gate-0: a bundle that stays refused (no or several equivalents, a merge) still blocks discard, because discard\_blocker (core/swarm\_out\_actor) needs the tips taken back. -- handled: out of this run's scope by the task's own rule 5 (another worker is changing the manual landing record, which is the way out for such a run, and settle/manual-landing code is not to be touched here); each such refusal names the takeover by hand → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 21/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 68e6031e8033: 4 pass, 0 fail, 0 not run
- test · swarmai @ c653b98c2d75: 7 pass, 0 fail, 7 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c9cb2b0bddbe/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_hive\_return.py tests/test\_elastic\_round\_liveness.py  → pass (exit 0, 146 tests on the reviewed tree)
- perl -e 'alarm 280; exec @ARGV' npx vitest run src/components/swarm-out/ReturnPanel.test.tsx src/i18n → pass (6 files, 31 tests)
- perl -e 'alarm 110; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c9cb2b0bddbe/repos/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass
- perl -e 'alarm 110; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c9cb2b0bddbe/repos/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- perl -e 'alarm 110; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_c9cb2b0bddbe/repos/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pass
- perl -e 'alarm 280; exec @ARGV' npx tsc --noEmit → pass
- perl -e 'alarm 200; exec @ARGV' npm run lint:contract → pass
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_c9cb2b0b → not\_run (environment: the installed SwarmAI reads features.yaml schema 1, the repo's is 2; the repo's own feature\_map (backend/.venv python -m scripts.feature\_map) reports all 16 reached walks not\_run: runtime)
- npm run build → not\_run (run once on the landed tree through task\_checks (the task's recorded check); tsc is green here)
- bash scripts/run\_bvt.sh → not\_run (run once on the landed tree through task\_checks (the task's recorded check))
- 4 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_c9cb2b0b: environment: the installed SwarmAI reads features.yaml schema 1, the repo's is 2; the repo's own feature\_map (backend/.venv python -m scripts.feature\_map) reports all 16 reached walks not\_run: runtime unavailable (the pinned Chromium lacks system libraries, no swarm-replay user on this host)
- swarmai · npm run build: run once on the landed tree through task\_checks (the task's recorded check); tsc is green here
- swarmai · bash scripts/run\_bvt.sh: run once on the landed tree through task\_checks (the task's recorded check)
- swarmai · npx vitest run --reporter=verbose: run once on the landed tree through task\_checks; the touched component and i18n suites ran here
- swarmai · python scripts/lint\_skills.py: no skill file changed; run through task\_checks on the landed tree
- swarmai · python scripts/lint\_experts.py: no expert file changed; run through task\_checks on the landed tree
- swarmai · bash scripts/sync-version.sh check: no version file changed; run through task\_checks on the landed tree
