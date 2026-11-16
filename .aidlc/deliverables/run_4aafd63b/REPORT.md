# # Fix: a session review attempted while the model is unreachable burns an attem… · Audit through DELIVER

Delivered: # Fix: a session review attempted while the model is unreachable burns an attempt and fails the health check \*\*Evidence (2026-11-16, this host's ingestion.db, last 7 days).\*\* Of 67 S7 review attempts. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 29m 48s wall · 29m 48s active · 60.0% budget |
| Quality | WARN | 1/1 verification · 4 not run · 7/8 findings closed (2 test-closed, confirmed by review) · 1 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 562k comparable · 445k input · 117k output · 20.7M cache reads, not counted · 21.2M billed · host-session responses in the run window · 57.5% budget |

Knowledge: requirement recalled · 8 · design recalled · 5 · cited 0 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which errors count as 'the model cannot be reached'? → Only the CLI's 'API Error: Could not load (AWS) credentials', 'API Error: Can't reach the API server' and an 'API Error:' report naming ENOTFOUND. Why: These are the evidenced texts; anchoring on 'API Error:' keeps a reply that merely mentions credentials (a session about AWS) from being postponed forever. Throttling, 5xx and expired-token 403s keep today's handling (an expired token is caught by the preflight on the next look) (DESIGN\_SPEC, agent)
- D2 · Should C2 keep counting environment errors? → No: C2 judges reviews that reached the model; unreachable attempts leave both attempts and errored, failed jobs whose error is unreachable leave failed, and the count is reported as value.unreachable. Why: An outage says nothing about whether reviews work, the worker no longer spends retries on it, and the existing week's rows must not fail the check; the count stays visible (DESIGN\_SPEC, agent)
- D3 · What does the preflight do on 'unknown' or a stale snapshot? → Proceed (fail open), like the chat preflight. Why: A DNS outage and a non-auth STS error both read 'unknown'; blocking on it would stop reviews for an STS policy problem that does not affect Bedrock. ENOTFOUND is then caught after the attempt by the classifier (DESIGN\_SPEC, agent)
- D4 · How often may a failing review retry during a DNS outage? → After an unreachable failure the executor claims nothing for POSTPONE\_DELAY (in-memory hold); the failed job is postponed by the same delay. Why: Without a lane hold every queued job would spawn a failing CLI each delay for the whole outage (DESIGN\_SPEC, agent)
- D5 · An account mismatch maps to readiness 'expired': postpone? → Yes. Why: The chat spawn refuses a mismatched account too; the review would run under the wrong account or fail the same way, and postponing spends nothing (DESIGN\_SPEC, agent)
- D6 · The S0 month-summary lane has the same misframing (model outage counted as a summary failure). → Out of this change; reported as a follow-up. Why: Its writer fails through the Bedrock client with botocore errors (llm.failure\_reason), a different path and classifier; widening this diff would reopen review for an unevidenced lane (DESIGN\_SPEC, agent)
- D7 · Test fixture RETRY\_DELAY → Keep the real RETRY\_DELAY in the new tests and make jobs due explicitly between drains. Why: With RETRY\_DELAY 0 a failed job is reclaimed in the same pass, which hides whether a drain stopped (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Call CredentialValidator.check() (or the SessionUnit preflight) from the review thread before each review. Not taken: check() keeps a single in-flight asyncio future bound to the main event loop and runs STS on the 'spawn' pool the chat path waits on; calling it from the review thread's own loop would share a future across loops and add STS calls to that pool. The readiness snapshot is the same result with no network and no pool.
- A3 · Record environment failures with a new extraction\_attempts status (e.g. 'unreachable') instead of classifying error text in C2. Not taken: status has a CHECK constraint, so it needs a schema migration of ingestion.db, and the week already recorded as 'failed' would still fail C2; classifying the recorded error text covers both old and new rows.
- A4 · Check credentials in drain before claiming and leave the job queued untouched. Not taken: The task asks the job to go through s2\_jobs.postpone, and ReviewPostponed in run\_session\_job is the existing, tested way the memory gate already does exactly this; one path for both waits.

Requirements analysis:

- missing\_edge (R2.2): A DNS outage passes the credential preflight (the STS check reads it as 'unknown' and fails open), so without a lane hold every queued job would run and fail once per postpone delay for the whole outage. → rewritten
- missing\_edge (R2.4): The recorded error carries up to 300 characters of what the CLI said; a session about AWS credentials could put the word in a non-API failure. The classifier is anchored on the CLI's 'API Error:' report to avoid postponing a real failure forever. → rewritten
- missing\_edge (R3.1): The attempts already recorded as failed with these errors stay in the database for the week; C2 must classify them by their error text, the same classifier the worker uses, so the existing week passes. → rewritten
- missing\_edge (R2.1): Sibling outside this change: the S0 month-summary lane (summary\_lane.SummaryLane.run\_due) also counts a model outage as a summary failure (summary\_failed after 3, a day's backoff). Its writer fails through the Bedrock client with botocore errors (core.ingestion.llm.failure\_reason), not the CLI's text, so it needs its own change; it is left out of this run and reported as a follow-up. → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- security · review r2: read 100% of its prompt · 3/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ f994cf9f196d: 3 pass, 0 fail, 0 not run
- test · swarmai @ 9d734aee407c: 9 pass, 0 fail, 4 not run
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

TEST · swarmai:
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_4aafd63bcf53/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_cdf\_review\_unreachable.py tests/test\_cdf\_health.py -q --timeout=60 -p no:cacheprovid → pass (the change's tests (preflight, unreachable postpone and lane hold, other errors unchanged, anchored classifier, relations outage, long-postponed stall, C2): 40 tests, exit 0)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_cdf\_status.py tests/test\_attention\_authority.py tests/test\_cdf\_s7\_review.py tests/test\_cdf\_agent\_decides\_review\_fixes.py tests/tes → pass (existing tests of the touched functions and their callers: 206 tests, exit 0)
- python scripts/smoke\_import.py → pass (640 modules import)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- python scripts/lint\_skills.py → pass (exit 0)
- python scripts/lint\_experts.py → pass (exit 0)
- bash scripts/sync-version.sh check → pass (versions match)
- bash scripts/run\_bvt.sh → pass (53 curated files, exit 0 (with the task's tool PATH, which carries uv))
- npx tsc --noEmit → not\_run (no desktop file changed; run on the task repo after landing through task\_checks)
- 3 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run on the task repo after landing through task\_checks
- swarmai · npm run lint:contract: no desktop file changed; run on the task repo after landing through task\_checks
- swarmai · npm run build: no desktop file changed; run on the task repo after landing through task\_checks
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run on the task repo after landing through task\_checks

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_cdf\_review\_unreachable.py
