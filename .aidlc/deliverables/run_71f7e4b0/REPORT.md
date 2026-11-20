# Build the Inbox core of design §5, §8, §9: a v20 store, a background runner, th… · Audit through DELIVER

Delivered: Build the Inbox core of design §5, §8, §9: a v20 store, a background runner, the click-only send path with a caller check, the inbox submit CLI, the /api/inbox routes of schemas/inbox.py, a Needs You. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | WARN | 1h 40m wall · 1h 40m active · 103.6% budget |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 13/14 findings closed (17 reported; within 10 lines of one file they count once) (12 test-closed, confirmed by review) · 1 HIGH finding open · +2 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 1.1M comparable · 854k input · 271k output · 54.6M cache reads, not counted · 55.7M billed · host-session responses in the run window · 84.4% budget |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 2×

## Decisions

- D1 · How are feed sources represented in inbox\_sources? → One row id 'feed', kind 'feed', backend 'feed', category feed, created and enabled while config.yaml has at least one enabled feed; its SourceConfig.options carries {'feeds': \[the enabled feed dicts\]} read from config.yaml at pull time. Why: The mockup's channel settings show one 信息流 row ('22 个 feed · 每 6 小时'); config.yaml stays the only feed store (design §2); the row holds only cursor and health (DESIGN\_SPEC, agent)
- D2 · Where does blocking work of the Inbox run? → Route-side file reads (config.yaml) on executors.run\_nav like the community routes; the psutil caller check and the runner's file reads on a module-local single-thread pool in core/inbox/store.py ('swarm-inbox'); DB through the aiosqlite pool; model calls inside the triage/draft/trend functions (their contract is async). Why: Keeps it off the event loop and off the pools the chat path waits on (spawn, gate, subprocess, turn\_\*); core/executors.py is not a file this run owns, so a central 'inbox' pool is proposed for merge (DESIGN\_SPEC, agent)
- D3 · What does POST /refresh pull? → Every enabled source now, ignoring interval and pull window, one at a time under the runner's pull lock, then it returns the counts. Why: It is the person's '现在刷新' click; a refresh that only pulls already-due sources would usually do nothing (DESIGN\_SPEC, agent)
- D4 · How is a send that never returns handled? → source.reply runs under asyncio.wait\_for(REPLY\_TIMEOUT\_S=120); a timeout is recorded unknown like any other unexpected exception. Why: A reply that hangs is the 'unknown' case of §8; the timeout only bounds a hang, the outcome is never retried (DESIGN\_SPEC, agent)
- D5 · Which own messages close items? → Open or snoozed REPLY / PROPOSED items of the same source and thread whose received\_at is at or before the own message. Why: Design §5: replied when the latest message in the thread is the person's; a later message from someone else stays open; a snoozed item would otherwise come back already answered (DESIGN\_SPEC, agent)
- D6 · Is the draft history from other threads? → Only the person's own messages inside the freshly fetched thread. Why: The Source contract has no call for 'my messages to this person'; drafts may never see another thread's text (§6.3). Reported for merge (DESIGN\_SPEC, agent)
- D7 · Does a pull back-fill auto send rows for own comments (design §8 last paragraph)? → No. Why: The runner cannot tell an outreach comment from any other own message; back-filling would turn every own message into an auto send. Reported for merge (DESIGN\_SPEC, agent)
- D8 · How long is 稍后 (later)? → 3 hours (SNOOZE\_S), woken back to open on the next list or count read. Why: Not specified; short enough to come back the same day (DESIGN\_SPEC, agent)
- D1 · Three existing migration tests assert user\_version == 19 and go red with v20; they are not in run A's file list. → Changed their assertion to CURRENT\_SCHEMA\_VERSION (one line each). Why: The v20 migration this run owns breaks them; none of the other three runs touch them, so no merge conflict; the version-agnostic form stops the next bump breaking them again (BUILD report · swarmai, agent)
- D2 · Where do the Inbox's blocking calls run, with core/executors.py not in this run's files? → A module-local two-thread pool in core/inbox/store.py ('swarm-inbox'); route-side config reads on executors.run\_nav like the community routes. Why: Off the event loop and off the chat pools; moving it into core/executors is a one-line follow-up at merge (BUILD report · swarmai, agent)
- D3 · Which POSTs require X-Swarm-User-Action? → send, not-sent, resend (with the caller check) and the body-less redraft, refresh, retry; action and the feed CRUD (JSON bodies, preflighted) do not. Why: A cross-site page can send a body-less POST without a preflight; the desktop client must send the header on these six (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Run the Inbox pulls through the existing job scheduler (a system job every 15 min). Not taken: The scheduler wakes hourly, computes cron in UTC and runs only in daemon/hive (design §9); it cannot give 15-minute pulls, would run on Hive, and would not run in subprocess mode where Windows/Linux desktops live.
- A3 · Guard the send route by a token only the desktop UI holds (written into backend.json or a header). Not taken: Any process the backend starts can read the same files and environment the UI reads, so a token does not tell the UI from an agent; the design (§8) decides by who holds the TCP connection.
- A4 · Give inbox submit an HTTP route the CLI calls. Not taken: Design §13 removed it: any local process could then create items pointing at any thread. The CLI writes data.db itself after the thread check, and refuses when the database is older than v20 instead of migrating it.

Requirements analysis:

- undefined\_term (R2.5): The contract has no per-feed source row; the mockup shows one 信息流 row ('22 个 feed'). The runner keeps one inbox\_sources row id 'feed', kind 'feed', whose SourceConfig.options carries the enabled feeds read from config.yaml at pull time; config.yaml stays the only feed store. → rewritten
- missing\_edge (R2.3): A rejected or failing triage batch: the items stay stored with their rule lane and triaged\_at empty, and are offered to triage again at the next pull of that source. → rewritten
- missing\_edge (R3.5): A reply that never returns: bounded by a timeout, recorded unknown, never resent. → rewritten
- vague (R6.1): POST /refresh 'pull every due source now' is read as: pull every enabled source now, ignoring interval and pull window (a person's click), then return the counts. → rewritten
- missing\_edge (R6.1): The daily trend summary needs storage the table list does not name; a sixth table inbox\_trend\_summaries holds it. → rewritten
- conflict (R5.2): Design §8 says a pull that sees an own comment back-fills a missing auto send row; the runner cannot tell an outreach comment from any other own message, so this run does not back-fill (would turn every own message into an auto send). Reported for merge. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 11/27 tool calls
- correctness · review r1: read 100% of its prompt · 12/27 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 6271cc807022: 6 pass, 1 fail, 0 not run
- test · swarmai @ 2d11476e2251: 8 pass, 0 fail, 4 not run
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
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_inbox\_store.py tests/test\_inbox\_runner.py tests/test\_inbox\_send.py tests/test\_inbox\_caller.py tests/test\_inbox\_submit.py tests/test\_inbox\_api.py tests/test\_inbox\_ → pass (90 tests: every Inbox invariant (store, runner, send path, caller check, submit, API, Needs You, privacy))
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_compact\_summary.py tests/test\_cdf\_sources\_core.py tests/test\_memory\_v2\_wiring.py tests/test\_recall\_metrics.py tests/test\_seed\_data → pass (245 existing tests of the touched modules: migrations, the backend program, Needs You, the router blocking-I/O and P7 scans, community feeds)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pre\_existing (the same 6 tests fail on the run's base with the same content: test\_cdf\_materializer\_static (core/feature\_map/ledger.py writers unlisted), test\_silent\_except\_baseline (core/feature\_map/files.py, core/)
- npx tsc --noEmit → not\_run (no desktop file changed in this run)
- 3 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: the same 6 tests fail on the run's base with the same content: test\_cdf\_materializer\_static (core/feature\_map/ledger.py writers unlisted), test\_silent\_except\_baseline (core/feature\_map/files.py, core/run\_protocol/engine.py), test\_precommit\_uv\_lock (TypeError in its git fixture); none in a file this 

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed in this run
- swarmai · npm run lint:contract: no desktop file changed in this run
- swarmai · npm run build: no desktop file changed in this run
- swarmai · npx vitest run --reporter=verbose: no desktop file changed in this run

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/inbox/caller.py
- swarmai: backend/core/inbox/runner.py
- swarmai: backend/core/inbox/send.py
- swarmai: backend/core/inbox/store.py
- swarmai: backend/core/inbox/submit.py
- swarmai: backend/routers/inbox.py
- swarmai: backend/tests/inbox\_fakes.py
- swarmai: backend/tests/test\_inbox\_api.py
- swarmai: backend/tests/test\_inbox\_attention.py
- swarmai: backend/tests/test\_inbox\_caller.py
- swarmai: backend/tests/test\_inbox\_runner.py
- swarmai: backend/tests/test\_inbox\_send.py
- swarmai: backend/tests/test\_inbox\_store.py
- swarmai: backend/tests/test\_inbox\_submit.py
