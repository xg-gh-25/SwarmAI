# Four measured session-runtime gaps get fixed: a model-access cache warmer, mid-… · Audit through DELIVER

Delivered: Four measured session-runtime gaps get fixed: a model-access cache warmer, mid-turn session ids saved for --resume, a graceful CLI close on idle reclaim, and the cold-start spawn overlapped with recal. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 35m wall · 1h 35m active · 98.5% budget |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 7/7 findings closed (6 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 1.3M comparable · 942k input · 324k output · 110.6M cache reads, not counted · 111.8M billed · host-session responses in the run window · 95.0% budget |

Knowledge: requirement none · 0 · design recalled · 7 · cited 1 (+4 found on demand) · searched the DDD 3×

## Decisions

- D1 · What counts as 'a tab is open' for the warmer? → Activity in the last 30 minutes: a chat send (router) or a frontend PUT of open-tabs (written on every tab add, close and switch). Why: open\_tabs.json survives app close, so its content is not liveness; the PUT and the send are what a live window does. 30 minutes bounds an idle window to two warm-ups (DESIGN\_SPEC, agent)
- D2 · Which teardowns get the graceful close? → kill() and \_crash\_to\_cold\_async() when the unit is IDLE with a live stream reader, except the poison-guard recycle and the dead-stream respawn on the send path (graceful=False there). Why: Those are the non-hung reclaims (TTL, orphan reap, release, eviction, refresh, shutdown); a STREAMING/WAITING/DEAD unit or a watchdog kill is the hung class and stays immediate; the send path keeps its latency (DESIGN\_SPEC, agent)
- D3 · How are a gracefully exited CLI's children handled? → Snapshot its descendants as psutil processes before the stdin close and SIGKILL the ones still running (psutil checks the create time, so a reused pid is never hit) after the CLI exits. Why: A child reparented to init after the CLI's own exit would escape the existing pgid/tree sweep, which looks the tree up from the CLI's pid (DESIGN\_SPEC, agent)
- D4 · How long is the grace? → 2 s (a healthy idle CLI exited in 20 ms in 8 of 8 measured closes). Why: Long enough for the exit-time writes, short enough that a shutdown of many idle sessions stays bounded (DESIGN\_SPEC, agent)
- D5 · What does the TTFT line's spawn segment mean for an early spawn? → The time send() waited for the early client (the part not hidden behind recall); spawn\_perf logs the full spawn and early=1. Why: Keeps TTFT = pre\_send + spawn + send+infer additive (DESIGN\_SPEC, agent)
- D6 · The CLI's real missing-transcript error ('No conversation found with session ID') did not match the session-not-found patterns, so R2.3's fallback never ran for it. → Added that pattern to \_SESSION\_NOT\_FOUND\_PATTERNS (measured message, CLI 2.1.301) and tested the fallback with it. Why: R2 sends more sessions down --resume after a restart; the fallback it relies on has to recognise the CLI's actual message (BUILD report · swarmai, agent)
- D7 · The early spawn starts a CLI before \_ensure\_spawned's credential pre-flight. → The pre-flight was extracted (\_credential\_preflight) and the early spawn runs it first. Why: An expired credential must not start a CLI that stalls under \_spawn\_lock and blocks every other tab's spawn (BUILD report · swarmai, agent)
- D8 · Tests that drive the real router or spawn path would now start real CLIs (early spawn, warmer loop). → Two autouse chokepoints in tests/conftest.py: start\_early\_spawn stubbed unless a test is marked early\_spawn; the warmer's loop never starts (a fresh instance per test). Why: One structural guard instead of per-test discipline, the same pattern as the credential pre-flight stub (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Item 1 as a fixed 14-minute background handshake loop. Not taken: Measured: a handshake that finds the entry fresh does not rewrite it (the 13:40 entry expired at 13:55:30 although handshakes ran at 13:46), so a fixed cadence leaves a stale window of up to 14 minutes every cycle; the warm-up has to follow the entry's own timestamp.
- A3 · Item 1 by setting CLAUDE\_CODE\_SKIP\_MODEL\_ACCESS\_MEMORY or another CLI switch so spawns never probe. Not taken: That switch disables the remembered verdicts (every start probes, the slow path every time); no CLI option makes the probe asynchronous.
- A4 · Item 5 by pre-warming a pool unit per tab (the maintained-pool design) instead of overlapping the first spawn with recall. Not taken: A pool unit cannot take --resume (the resume id is fixed at spawn, Brain entry e644c372) and adds a standing process per tab; the overlap needs no extra process and keeps the one spawn path.
- A5 · Item 4: point SwarmAI's CLIs at their own CLAUDE\_CONFIG\_DIR with a copy migration of existing transcripts. Not taken: Measured not needed: SwarmAI never resumes a transcript older than about 25 h (IDLE TTL 24 h, COLD purge 1 h, restart state file 24 h), and the person's cleanup only deletes transcripts untouched for 30 days; the move would relocate plugins (plugin\_manager writes \~/.claude/plugins), the model-access cache and every transcript reader, and leak into the person's own claude in a SwarmAI terminal.

Requirements analysis:

- missing\_edge (R1.1): Measured: a handshake that finds the entry fresh does not rewrite it (13:46 handshakes left the 13:40 entry unchanged and it expired at 13:55:30), so a fixed 14-minute cadence would not keep it fresh; the warm-up is scheduled from the entry's own timestamp, just after it expires. → rewritten
- missing\_edge (R1.1): The cache key hashes the environment (including AWS\_ACCESS\_KEY\_ID) and the settings' awsCredentialExport, so the job executor's CLIs (boto3 keys injected) write other keys than chat. The warm-up must use the chat spawn's own settings and environment, and learns the chat key from the one entry its handshake rewrote. → rewritten
- conflict (R2.1): session\_state\_persistence documented that resuming mid-stream corrupts context (PE F1). Measured with the real CLI: a session SIGKILLed during a Bash call and during an unanswered permission prompt resumed with the same id, its transcript prefix unchanged, and the CLI added a tool\_result 'Tool call interrupted' for the open call; the model reported the command and that it did not finish. The rule is replaced by the measurement. → rewritten
- missing\_edge (R3.1): Measured: an immediate SIGKILL after the result kept the assistant reply in 4 of 4 trials (the issue-625 loss did not reproduce for an idle CLI), but lost the exit-time records cost-state (4 of 4) and last-prompt (2 of 2 two-turn trials) that a stdin close writes; the stdin close took 20 ms. cost-state is what the CLI restores on --resume and what usage\_accounting reads as the resumed process's starting totals. → rewritten
- vague (R4.1): Item 5 depends on the system prompt no longer carrying the recall block. That change (c788c736c, recall rides the first query) is in this task's base (418d9417d) but not in origin/main as fetched here; the early spawn uses options built before recall and is valid only on top of it. → confirmed
- missing\_edge (R1.1, R4.1): Item 4 (own transcript home) is not built: measured, a person's own Claude Code start does delete a 40-day-old transcript in the shared config dir while SwarmAI's spawn (--setting-sources=project) skips cleanup, but SwarmAI never resumes a transcript older than about 25 hours (IDLE units are killed after 24 h, COLD units purged after 1 h, the restart state file is discarded after 24 h), far inside the 30-day default; the move would relocate plugins, settings and every transcript reader for no measured runtime gain. → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 9/26 tool calls
- correctness · review r1: read 100% of its prompt · 25/26 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 16/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 704519ed469a: 5 pass, 0 fail, 0 not run
- test · swarmai @ ff705079e487: 7 pass, 0 fail, 4 not run
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
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python scripts/smoke\_import.py → pass
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python scripts/lint\_problem\_values.py → pass
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python scripts/security\_scan.py → pass
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python scripts/lint\_skills.py → pass
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python scripts/lint\_experts.py → pass
- perl -e 'alarm 60; exec @ARGV' bash scripts/sync-version.sh check → pass
- PYTEST\_ADDOPTS=--maxfail=1000 perl -e 'alarm 590; exec @ARGV' bash scripts/run\_bvt.sh → pre\_existing (the only failures are the 3 tests of tests/test\_precommit\_uv\_lock.py, which need a uv binary on PATH (none on this machine: TypeError on shutil.which('uv') None); they fail identically on the run's ba)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_model\_access\_warmer.py tests/test\_early\_spawn.py tests/test\_graceful\_cli\_close.py tests/test\_session\_state\_persistence.py tests/te → pass
- npx tsc --noEmit → not\_run (backend-only change (no file under desktop/); the worktree has no node\_modules; run through task\_checks on the landed checkout)
- npm run lint:contract → not\_run (backend-only change; run through task\_checks on the landed checkout)
- 2 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · PYTEST\_ADDOPTS=--maxfail=1000 perl -e 'alarm 590; exec @ARGV' bash scripts/run\_bvt.sh: the only failures are the 3 tests of tests/test\_precommit\_uv\_lock.py, which need a uv binary on PATH (none on this machine: TypeError on shutil.which('uv') None); they fail identically on the run's base tree (pipeline-v1 base-tree, 3 failed). Every other BVT file passes, including test\_silent\_except

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: backend-only change (no file under desktop/); the worktree has no node\_modules; run through task\_checks on the landed checkout
- swarmai · npm run lint:contract: backend-only change; run through task\_checks on the landed checkout
- swarmai · npm run build: backend-only change; run through task\_checks on the landed checkout
- swarmai · npx vitest run --reporter=verbose: backend-only change; run through task\_checks on the landed checkout

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/model\_access\_warmer.py
- swarmai: backend/tests/test\_early\_spawn.py
- swarmai: backend/tests/test\_graceful\_cli\_close.py
- swarmai: backend/tests/test\_model\_access\_warmer.py
