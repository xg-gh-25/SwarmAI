# ## Goal · Audit through DELIVER

Delivered: ## Goal On Windows, closing or killing a chat session crashes inside SessionUnit and leaves the Claude CLI process (and its children) running. Fix it so a session kill works on Windows, macOS and Linu. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 30m 55s wall · 30m 55s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 2 not run · 2 red at base · 5/8 findings closed (3 test-closed, confirmed by review) · 1 fail-to-pass · 1 HIGH finding open |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 609k comparable · 472k input · 138k output · 22.8M cache reads, not counted · 23.4M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 2 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · Use psutil for the reaper match on every platform or only on Windows? → Only on Windows; macOS/Linux keep pgrep -f. Why: The task allows either and requires today's POSIX behaviour; pgrep is proven there and its tests stay unchanged (DESIGN\_SPEC, agent)
- D2 · How does the Windows own-instance orphan test replace ppid() == 1? → psutil.Process(pid).parent() is None (the parent is gone or the pid was reused by a later process). Why: Windows never reparents to pid 1; parent() checks the parent's create time, so it is the Windows equivalent of 'reparented' (DESIGN\_SPEC, agent)
- D3 · Out-of-path siblings (project\_agent\_session.py killpg, aws\_detect.py killpg)? → A separate small commit after this run lands. Why: They are not on the session kill/watch/reap path; widening this diff reopens its review (AGENT: elsewhere, pointed out in passing) (DESIGN\_SPEC, agent)
- D4 · Restructure \_force\_kill by extracting the POSIX path or by branching? → kill\_now flag + 'if kill\_now and is\_windows(): ... elif kill\_now:' so the POSIX try block keeps its indentation and text. Why: Smallest diff on the proven POSIX path; extraction would mix an extract with the fix (R4) (BUILD report · swarmai, agent)
- D5 · \_kill\_tree\_psutil return shape → (descendants in snapshot, descendants killed, cli\_killed). Why: The startup sweep counts a leftover as killed only when the CLI itself was killed (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A sys.platform branch with inline psutil code at each of the \~11 sites. Not taken: Copies the same psutil branch 11 times with drift risk; Gate-0 round 2 weighed it and it loses on simplicity.
- A3 · Switch every platform to psutil (drop os.kill(pid, 0), pgrep and killpg). Not taken: Changes the proven POSIX behaviour (the process-group sweep that catches processes reparented to init, pgrep's view of other users' processes) and every POSIX test; the task requires POSIX to stay as today.

Requirements analysis:

- missing\_edge (R1.3, R2.3): On Windows os.kill(pid, 0) is GenerateConsoleCtrlEvent(CTRL\_C\_EVENT) — it raises OSError for a dead pid (not ProcessLookupError) or sends Ctrl-C; \_await\_process\_exit and the owner-liveness checks use it, so the kill would still abort after the AttributeError fix → rewritten
- missing\_edge (R1.1): On Windows os.kill(dead\_pid, SIGTERM) raises a plain OSError (WinError 87), which \_kill\_pids does not catch; the Windows tree kill therefore goes through psutil.Process.kill, which also guards pid reuse by create time → rewritten
- missing\_edge (R1.2): The graceful path catches Exception, so its os.getpgid AttributeError does not crash but makes every Windows graceful close fall back to the SIGKILL sweep (losing the CLI's exit records) → rewritten
- missing\_edge (R2.1): Windows command lines use backslash paths; the claude pattern uses forward slashes, so the cmdline is normalised to '/' before matching; Windows does not reparent orphans to pid 1, so the own-instance orphan test checks that the parent is gone → rewritten
- missing\_edge (R3.1, R3.3, R3.4): Gate-0 round 1: the PID watchdog, health\_check and the streaming orchestrator error path also probe a live CLI with os.kill(pid, 0); on Windows that is CTRL\_C\_EVENT, and the uncaught OSError in the watchdog or the caught one in the orchestrator drops a live CLI → rewritten
- missing\_edge (R3.5): Gate-0 round 2: project\_agent\_session.py:351 and aws\_detect.py:193 have the same unguarded os.killpg shape outside the session path; they are fixed as a separate small change after this run lands, and a static test keeps os.kill(pid, 0) out of the session-path modules → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 3/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 3/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/26 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ eec91be0f86a: 5 pass, 0 fail, 0 not run
- test · swarmai @ 2778756a02a1: 8 pass, 0 fail, 2 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- perl -e 'alarm 100; exec @ARGV' .venv/bin/python -m pytest tests/test\_windows\_process\_kill.py -q --timeout=60 → pass (21 passed: Windows path forced; fail-to-pass against the base (the module process\_compat and every fixed branch are missing there))
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_windows\_process\_kill.py tests/test\_force\_kill\_pgid.py tests/test\_graceful\_cli\_close.py tests/test\_session\_kill\_recovery.py tests/t → pass (141 passed: the POSIX kill, graceful close, startup sweep, reaper and ownership tests stay green)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_session\_unit\_recovery\_paths.py tests/test\_output\_liveness\_watchdog.py tests/test\_tool\_free\_kill\_paths.py tests/test\_process\_resour → pass (green (ran with test\_deploy\_hardening.py: 98 tests, the only failures the 7 rsync ones below))
- perl -e 'alarm 100; exec @ARGV' .venv/bin/python -m pytest tests/test\_deploy\_hardening.py -k "not TestInstallerOnedir and not TestShellDeployFailures" -q --timeout=60 --maxfail=1000 → pass (incl. TestExpectedKillLogFilter (anchor fixed in this run; red on base))
- perl -e 'alarm 100; exec @ARGV' .venv/bin/python -m pytest tests/test\_deploy\_hardening.py -k "TestInstallerOnedir or TestShellDeployFailures" -q --timeout=60 --maxfail=1000 → pre\_existing (7 tests fail on base and here the same way: 'rsync: command not found' on this host)
- python scripts/smoke\_import.py → pass (All 694 modules import)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- SWARMAI\_SUITE=1 .venv/bin/python -m pytest $(bash ../scripts/run\_bvt.sh --list \| grep -v test\_precommit\_uv\_lock) -q --timeout=60 --maxfail=1000 → pass (1331 BVT tests pass (the manifest minus test\_precommit\_uv\_lock))
- bash scripts/run\_bvt.sh → pre\_existing (only tests/test\_precommit\_uv\_lock.py fails: the uv binary is not installed on this host (TypeError from subprocess with UV=None); every other BVT file passes (row above))
- 2 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · perl -e 'alarm 100; exec @ARGV' .venv/bin/python -m pytest tests/test\_deploy\_hardening.py -k "TestInstallerOnedir or TestShellDeployFailures" -q --timeout=60 --maxfail=1000: 7 tests fail on base and here the same way: 'rsync: command not found' on this host
- swarmai · bash scripts/run\_bvt.sh: only tests/test\_precommit\_uv\_lock.py fails: the uv binary is not installed on this host (TypeError from subprocess with UV=None); every other BVT file passes (row above)

## Not run (the agent's reason, not checked)

- swarmai · python -m scripts.feature\_map replay --affected --run run\_0e599307: 16 affected walks not run, runtime unavailable on this Hive: the pinned Chromium lacks its system libraries and there is no swarm-replay user (hive/replay-host.sh)
- swarmai · skills-lint, experts-lint, desktop-typecheck, desktop-contract-lint, desktop-build, version-check, frontend-run-frontend-tests: the change touches no skill, expert, desktop or version file; each is run through task\_checks on the landed branch before delivery

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/process\_compat.py
- swarmai: backend/tests/test\_windows\_process\_kill.py
