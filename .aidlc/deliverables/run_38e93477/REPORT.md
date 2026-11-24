# ## Goal: a dispatch card appears in the dispatching chat for every Swarm Out ta… · Audit through DELIVER

Delivered: ## Goal: a dispatch card appears in the dispatching chat for every Swarm Out task the session sends, however the dispatch command was written The backend writes a \`swarm\_out\_dispatch\` card after a Bas. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | BLOCK | 31m 42s wall · 31m 42s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 4/6 findings closed (8 reported; within 10 lines of one file they count once) (2 test-closed, confirmed by review) · 1 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | BLOCK | 543k comparable · 407k input · 136k output · 22.0M cache reads, not counted · 22.5M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 3 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · Where is 'this call dispatched task X' decided? → From the dispatch-log row the daemon wrote (dispatched\_from = the session, sent\_at inside \[call seen - skew, result arrived\]), never from the command text; the literal path keeps its text-based card shapes (refusal, not sure) because a refusal writes no row. Why: the producer-assigned id is the only identity every command shape shares (DESIGN\_SPEC, agent)
- D2 · Does the index lookup run on the event loop or in a pool? → The skip check (dict membership) and the claim (in-memory filter under a lock the writer holds only for a list append) run on the event loop; only drawing a claimed row (reading the log, building the card) goes to the io pool, as today's literal card does. Why: the lookup does no I/O and measures in microseconds; a pool hop per Bash result would add a queue wait on the io pool the chat path's file-change resolution waits on, which the task's watch item forbids. Nothing blocking runs on the loop (DESIGN\_SPEC, agent)
- D3 · What happens to the row a literal 'not sure' (maybe\_sent / printed nothing, no single row) call may have written? → Its rows in the window with the call's literal title (all rows in the window when the title is expanded or absent) are claimed silently: the call keeps today's single not-sure card. Why: otherwise the same dispatch would show as both 'not sure' and 'dispatched' (DESIGN\_SPEC, agent)
- D4 · A Swarm Run child dispatched through a wrapper (Gate-0 finding) → Its row claims its run id once per session; an unclaimed run draws swarm\_run\_card's card only when the run's first children were sent in this call's window (the same rule as the literal path, now swarm\_run\_card.card\_of\_run); never a per-child dispatch card. Why: same root cause as the dispatch card; the literal child path keeps its card and claims the run id first (DESIGN\_SPEC, agent)
- D5 · What does the index hold, given the pitfall that a second in-memory copy of a durable store goes stale? → Only fields a row never changes after it is written (id, sent time, session, title, lead run); the card's content is read from the log row when drawn. Why: rows are append-only and written once, so these fields cannot go stale; nothing the card shows is served from memory (DESIGN\_SPEC, agent)
- D6 · The task says the lookup runs off the event loop; the watch item says it stays off the pools the chat path waits on → the skip and the claim run on the event loop as pure in-memory operations (measured 0.07 us and 0.8 us); only drawing a new row (reading the log) goes to the io pool, once per dispatch. Why: nothing blocking runs on the loop, and a pool hop per Bash result would queue on the io pool the chat path's file-change resolution uses (BUILD report · swarmai, agent)
- D7 · How is the per-test isolation of the index kept? → an autouse reset in tests/conftest.py next to the other process-state resets. Why: the dispatch log is per test (\_isolate\_app\_data\_dir) and tests reuse dispatch ids and the session sess-test; without it a claim from one test suppresses a card in the next (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Widen \_DISPATCH\_RE / resolve wrapper scripts' text to recognise more command shapes. Not taken: a script's body is not visible from the command line; any new wrapper, alias or bash -c breaks it again. Identity stays on text, which is the root cause.
- A3 · After every Bash result, read and parse the whole dispatches.jsonl (swarm\_out.\_read\_log) and filter by dispatched\_from and time. Not taken: a full file read and JSON parse on every Bash result of every session, on the streaming path, growing with the log; not one indexed lookup.
- A4 · Dedupe by querying the session's persisted messages for an existing swarm\_out\_dispatch block with the dispatch id. Not taken: a database query per candidate on the streaming path, and cards of the running turn may not be written yet when a parallel call's result arrives; the in-process claimed set is exact for every row the index holds, and the index holds every row a live call's window can contain.

Requirements analysis:

- missing\_edge (R2.3): two parallel Bash calls whose windows both contain a row: both would draw it; the index keeps a per-dispatch claimed mark (set atomically by whoever draws the card first, the literal path included) so the second draws none → rewritten
- missing\_edge (R2.2): a literal dispatch that answered \`maybe\_sent\` writes a session-less row with dispatched\_from; a sweep would draw it as a sent card next to the not-sure card. The rows of that call (in window, same literal --title) are claimed silently so today's single not-sure card stays → rewritten
- missing\_edge (R3.1): the index holds only rows this daemon wrote: the daemon is the dispatch log's only writer (the CLI goes through its HTTP route) and a Bash call cannot outlive the daemon that streams it, so a row from before a restart can never be inside a live call's window → rewritten
- missing\_edge (R1.1): the window's start is the moment the orchestrator saw the tool\_use minus the card module's existing skew (sent\_at is second-truncated); its end is the moment the result arrived → rewritten
- missing\_edge (R2.6): Gate-0 round 1: swarm\_run\_card.watched has the same text-gated root; the sweep handles rows with parent\_run by drawing the run card (claimed per run\_id per session) instead of leaving wrapped child dispatches without a card → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 6/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 15/26 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 0cc1441b271d: 3 pass, 0 fail, 0 not run
- test · swarmai @ 38c1cbf96f9e: 9 pass, 0 fail, 5 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_38e934777deb/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_card.py tests/test\_swarm\_out\_dispatch\_inde → pass (286 tests; on the base orchestrator 9 of test\_swarm\_out\_dispatch\_index.py's 13 fail)
- perl -e 'alarm 115; exec @ARGV' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_38e934777deb/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_host.py tests/test\_swarm\_out\_deadline.py t → pass
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pass (1363 tests; the venv's and the task's uv on PATH)
- npx tsc --noEmit → not\_run (the change touches no desktop file; run on the landed branch through scripts.task\_checks)
- 4 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: the change touches no desktop file; run on the landed branch through scripts.task\_checks
- swarmai · npm run lint:contract: the change touches no desktop file; run on the landed branch through scripts.task\_checks
- swarmai · npm run build: the change touches no desktop file; run on the landed branch through scripts.task\_checks
- swarmai · npx vitest run --reporter=verbose: the change touches no desktop file; run on the landed branch through scripts.task\_checks
- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_38e93477: environment: the installed SwarmAI reads features.yaml schema 1 (the repo's is 2); with the worktree's own feature\_map all 16 reached walks are not\_run, runtime unavailable: this Hive's pinned Chromium lacks system libraries (libX11, libnss3, ...) and it has no swarm-replay user

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/swarm\_out\_dispatch\_index.py
- swarmai: backend/tests/test\_swarm\_out\_dispatch\_index.py
