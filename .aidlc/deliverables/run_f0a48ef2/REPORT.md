# # Fix: a session whose turn was stopped never gets its post-session hooks · Audit through DELIVER

Delivered: # Fix: a session whose turn was stopped never gets its post-session hooks \*\*Evidence (2026-11-15).\*\* A user Stop (any non-autonomous \`interrupt()\`, also the compaction guard's) recycles the subprocess. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 29m 38s wall · 29m 38s active · no baseline yet (3/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 3/5 findings closed (3 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 596k comparable · 473k input · 122k output · 24.0M cache reads, not counted · 24.6M billed · host-session responses in the run window · no baseline yet (3/5 runs) |

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 2 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · Should the COLD sweep be limited to the user-Stop recycle? → No: any COLD unit that ran a turn in this daemon and has no hooks enqueued qualifies. Why: The loss is the same for every recycle after a turn (Stop, compaction, SSE disconnect, steer, retry give-up); keying on the turn, not on the path, fixes the class and needs no per-path code (DESIGN\_SPEC, agent)
- D2 · Should shutdown (disconnect\_all) also fire hooks for COLD units still in the grace? → No change. Why: An IDLE unit inside the grace at shutdown already gets hooks there only because it is alive; a COLD one in the grace is caught by the next startup sweep (24 h), and the enqueued flag keeps both from firing twice. The task asks only that shutdown not fire twice (DESIGN\_SPEC, agent)
- D3 · Does the discard refusal wording in swarm\_out\_actor.\_lessons\_due need a change? → No change. Why: It says the lessons come about REVIEW\_DELAY+2 minutes after the last turn and at most MAX\_WAIT hours later; with the hooks fired 120 s after the stop, that becomes true for stopped sessions (DESIGN\_SPEC, agent)
- D4 · Is router.is\_busy enough to keep the COLD sweep off a unit whose retry is between attempts? → Also skip a unit with \_retrying True. Why: A retry's backoff leaves the unit COLD; is\_busy sees it only through the hub's open turn, which a direct unit.send does not register. \_retrying is set and cleared in a finally by RetryManager, so it cannot stick (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · The interrupt recycle fires the hooks itself (or marks the unit and the DEAD sweep fires them). Not taken: It skips the grace (a user who sends again right away would get hooks for a turn that is about to continue, breaking R1.3), and it covers only the user Stop: the SSE-disconnect, steer and give-up recycles move a unit that had a turn to COLD the same way.
- A3 · Initialise \_hooks\_enqueued to True for every new unit (nothing owed) and keep the IDLE/COLD sweep on that flag alone. Not taken: It changes the meaning of one flag read by seven sites (TTL, orphan reap, eviction, shutdown, DEAD sweep, memory pressure, RSS restart): an IDLE unit that never streamed would silently stop getting hooks on those paths. A separate marker keeps every existing reader as it is.

Requirements analysis:

- missing\_edge (R1.1): The same loss happens for every path that moves a unit that had a turn to COLD without hooks (the SSE-disconnect and steer recycles, a crash whose retries gave up), not only the user Stop; keying the sweep on 'had a turn in this daemon' covers that class without naming each path. → rewritten
- missing\_edge (R2.2): The TTL kill and orphan reap call enqueue\_hooks\_for\_unit without setting \_hooks\_enqueued; once COLD units are swept, they would fire a second time. Setting the flag inside enqueue\_hooks\_for\_unit closes that. → rewritten
- missing\_edge (R2.4): A COLD unit between retry attempts or mid-Stop is busy; the sweep skips it so hooks do not fire for a turn that is still running. → rewritten
- missing\_edge (R1.1): Shutdown (disconnect\_all) only takes alive units; a stopped unit still inside the grace at shutdown is left to the startup sweep (24 hours), as an IDLE unit inside the grace is today. The discard refusal's wording (about REVIEW\_DELAY+2 minutes after the last turn, at most MAX\_WAIT hours) becomes true for stopped sessions and needs no change. → rewritten
- missing\_edge (R2.4): The compaction guard's interrupt (autonomous=False) recycles the unit like a user Stop; while it runs, \_stopping makes the session busy, so the sweep fires nothing mid-recycle; once the turn has ended, the hooks follow after the grace like any other ended turn. → rewritten
- missing\_edge (R1.5): The no-client Stop branch went DEAD-&gt;COLD without stamping last\_used, so the grace would have been measured from an older time. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ abf1f536a7f7: 6 pass, 0 fail, 0 not run
- test · swarmai @ 04e4f56d7d7d: 12 pass, 0 fail, 5 not run
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
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_stopped\_session\_hooks.py tests/test\_cleanup\_dead\_toctou.py tests/test\_interrupt\_recycle.py tests/test\_lifecycle\_watchdog.py tests/ → pass (169 passed)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_chat\_scenarios\_e2e.py tests/test\_client\_io\_serialization.py tests/test\_eviction\_grace\_period.py tests/test\_eviction\_queue\_before\_f → pass (113 passed)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_session\_drain.py tests/test\_session\_kill\_recovery.py tests/test\_session\_router\_wake\_grace.py tests/test\_session\_unit\_cleanliness.p → pass (243 passed)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_stopped\_session\_hooks.py tests/test\_cleanup\_dead\_toctou.py tests/test\_lifecycle\_watchdog.py tests/test\_session\_unit\_dead\_resume.py → pass (100 passed after the \_cleanup\_dead change (the three batches above ran before it; this covers its tests))
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python -m pytest tests/test\_stopped\_session\_hooks.py -q --timeout=60 → pass (15 passed on the final tree)
- scripts.mutation\_check (12 mutations over the fix, the flag ordering and the router callers) -- pytest tests/test\_stopped\_session\_hooks.py → pass (14 of 14 mutations caught (the fix, the flag ordering in every hook path, the router callers, the DEAD sweep))
- backend/.venv/bin/python scripts/smoke\_import.py → pass (exit 0)
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (exit 0)
- backend/.venv/bin/python scripts/security\_scan.py → pass (exit 0)
- backend/.venv/bin/python scripts/lint\_skills.py → pass (exit 0)
- 7 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: run through task\_checks on the landed branch after DELIVER
- swarmai · npx tsc --noEmit: no desktop file changed; run through task\_checks on the landed branch
- swarmai · npm run lint:contract: no desktop file changed; run through task\_checks on the landed branch
- swarmai · npm run build: no desktop file changed; run through task\_checks on the landed branch
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run through task\_checks on the landed branch

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_stopped\_session\_hooks.py
