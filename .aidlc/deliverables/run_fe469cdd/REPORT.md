# # Fix: the backend launcher starts a full daemon on an argument it does not know · Audit through DELIVER

Delivered: # Fix: the backend launcher starts a full daemon on an argument it does not know \*\*Evidence (2026-11-15).\*\* On a Hive, an agent ran \`"$SWARM\_BACKEND\_BIN" -c "..."\` expecting a Python interpreter. \`bac. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 37m 30s wall · 37m 30s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 4 not run · 7/9 findings closed (4 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 508k comparable · 400k input · 107k output · 16.9M cache reads, not counted · 17.4M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 0 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · What does -h/--help do? → print the usage to stdout and return 0 without starting anything. Why: help is a request for usage, not a mistake; CI's build-macos.yml runs the binary with --help (DESIGN\_SPEC, agent)
- D2 · Exit code for an unknown first argument? → 2. Why: the usage-error convention argparse and the pipeline CLI already use (DESIGN\_SPEC, agent)
- D3 · Do later unknown flags after a known first daemon flag still start the daemon? → yes, run\_daemon keeps parse\_known\_args. Why: the crash-loop guard for wrapper/Tauri flags stays; only the first argument decides whether this is a daemon invocation (DESIGN\_SPEC, agent)
- D4 · ServiceManager.start\_all ownership default → keyword-only owns\_discovery=False (fail closed); stop\_all removes backend.port only if this manager wrote it. Why: a caller that forgets the flag can never overwrite or delete the owner's port file (DESIGN\_SPEC, agent)
- D5 · Gate heartbeat, channel gateway and the workspace surface watcher too? → no, out of this change. Why: the heartbeat file is the Tauri watchdog's liveness contract and the gateway has its own connection handling; changing them is a separate, riskier change; the launcher fix removes the accidental second backend that triggered this (DESIGN\_SPEC, agent)
- D6 · Accept the bare word 'help' too? → yes, same as -h/--help. Why: it is what a person types after seeing the usage; it starts nothing (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep the daemon as the fallthrough but reject only arguments that look like interpreter flags (-c, -m, -). Not taken: a deny-list: the next unexpected word (a typo of a subcommand, a new interpreter flag) starts a daemon again; the incident class is any unknown argument
- A3 · Give each shared side effect its own single-instance file lock (port file lock, scheduler lock, sweep lock). Not taken: N locks for one fact the backend already computes (who owns backend.json); backend.port has no lock to share and a lock-holder that is not the discovered backend would still point services at the wrong port
- A4 · Have ServiceManager read backend.json itself and compare its pid with os.getpid(). Not taken: duplicates main.py's ownership logic (path, liveness, lock) inside a second module; passing the value main.py already has keeps one decision point

Requirements analysis:

- missing\_edge (R1.3): --help/-h is a request for usage, not a mistake; CI build-macos.yml runs the binary with --help → rewritten
- missing\_edge (R1.2): after a known first daemon flag, later unknown flags are still ignored by run\_daemon (parse\_known\_args, the 2026-04-02 crash-loop guard); only the first argument decides whether the daemon starts → rewritten
- missing\_edge (R2.1): the default for start\_all ownership is fail-closed (no write) so a caller that forgets the flag cannot overwrite the owner's file → rewritten
- missing\_edge (R3.1): Gate-0 round 1: the scheduler and startup sweep share the backend.port ownership gap (jobs/scheduler.py has only a state.json lock, no single-instance lock) → rewritten
- missing\_edge (R3.1): Gate-0 round 2: usage-hive-sync and swarm-out-refresh are the same per-data-dir shape; usage rows are keyed by Hive row id (idempotent) but the usage\_sync.json cursor and the refresh loop's session wakes are not, and a non-owner backend is never the one a client talks to, so gating them loses nothing → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 7/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 16/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 642750c7156b: 6 pass, 0 fail, 0 not run
- test · swarmai @ 6267a3fc5bb6: 8 pass, 0 fail, 4 not run
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
- perl -e 'alarm 110; exec @ARGV' python -m pytest tests/test\_backend\_program.py tests/test\_service\_manager.py tests/test\_kill\_audit.py -q --timeout=60 -p no:cacheprovider --maxfail=1000 → pass
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pass
- npx tsc --noEmit → not\_run (the change touches no desktop file; it runs through task\_checks on the landed branch)
- npm run lint:contract → not\_run (the change touches no desktop file; it runs through task\_checks on the landed branch)
- 2 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: the change touches no desktop file; it runs through task\_checks on the landed branch
- swarmai · npm run lint:contract: the change touches no desktop file; it runs through task\_checks on the landed branch
- swarmai · npm run build: the change touches no desktop file; it runs through task\_checks on the landed branch
- swarmai · npx vitest run --reporter=verbose: the change touches no desktop file; it runs through task\_checks on the landed branch
