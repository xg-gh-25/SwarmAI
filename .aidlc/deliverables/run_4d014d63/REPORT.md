# Make background jobs and the daemon platform- and auth-mode-neutral: explicit B… · Audit through DELIVER

Delivered: Make background jobs and the daemon platform- and auth-mode-neutral: explicit Bedrock key in the job CLI env, login PATH from the user's own shell, Windows-safe pwd import, darwin-only launchctl unins. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 43m 29s wall · 43m 29s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 2 red at base · 6/8 findings closed (5 test-closed, confirmed by review) · 1 fail-to-pass · CF-5a36d19d: no mutation check (the fix is the test's own isolation; the proof is the reviewer's own order (this test, then test\_feature\_map\_cli.py and test\_feature\_map\_sandbox.py) passing, wh) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 592k comparable · 468k input · 124k output · 22.5M cache reads, not counted · 23.1M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 2 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · Replace PATH with the login PATH or merge? → merge: the login PATH's entries first, then inherited entries it does not have. Why: DDD guideline 7e01530c; a daemon-set entry (the backend venv) is not dropped by a login profile that does not list it (DESIGN\_SPEC, agent)
- D2 · Which shell when $SHELL is unset (launchd daemons often have none)? → $SHELL, then the account's login shell from pwd, then /bin/bash, then /bin/sh, the first that exists. Why: the account's login shell is the user's shell on macOS where the default is zsh; matches terminal.rs pick\_unix\_shell's $SHELL -&gt; /bin/bash order (DESIGN\_SPEC, agent)
- D3 · bedrock\_key mode with no saved key → behave as before (inject aws\_creds). Why: nothing to inject; leaving the CLI with no credential route at all is worse (DESIGN\_SPEC, agent)
- D4 · Item 6 (summarization/session\_scorer credential failures) → not done. Why: attention\_authority.\_needs\_account reads only extraction\_attempts (source CHECK S0-S7), ingestion\_s2\_jobs and poison events; recording a summarizer or scorer failure there fabricates a CDF record and changes CDF status, so it needs a reader change in core/attention\_authority.py, which another worker owns (DESIGN\_SPEC, agent)
- D5 · pwd inside functions or guarded at module top? → guarded at module top (pwd = None on ImportError). Why: existing tests patch replay\_user.pwd.getpwnam; the guard keeps them and still imports on Windows (BUILD report · swarmai, agent)
- D6 · Translate only the named fragment or every CJK fragment of the new-Brain AGENTS.md template? → every fragment (冷启动, 养成, 全貌, 養成) in that template and its comment. Why: the template must read as plain English; leaving others makes the test's no-CJK assertion impossible (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Set AWS\_BEARER\_TOKEN\_BEDROCK into os.environ once at daemon startup (call \_configure\_claude\_environment from main). Not taken: a process-wide variable is read by botocore at signing time and would switch sigv4 clients to the token (DDD decision 148515bd), and it misses mode switches after startup
- A3 · Keep a second hand-written list of governed files in the sentinel and add TIMELINE. Not taken: the two lists already drifted once; the task asks for one source

Requirements analysis:

- missing\_edge (R1.1): bedrock\_key mode with no key saved: there is nothing to inject, so the env falls back to the existing behaviour (aws\_creds injected) rather than leaving the CLI without any credentials → rewritten
- missing\_edge (R2.1): an interactive login shell can print banners before PATH; the probe prints PATH behind a marker and only the marked line is used → rewritten
- missing\_edge (R2.1): fish prints $PATH as a space list; the probe prints "$PATH" quoted, which bash/zsh/sh/fish all print colon-joined → rewritten
- missing\_edge (R4.1): backend/channels/install\_backend\_daemon.py uninstall() has the same unguarded launchctl shape; it is CLI-only and outside the files this task may edit (nine workers share the repo), so it is reported, not changed → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 7/25 tool calls
- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 15/25 tool calls
- correctness · review r2: read 100% of its prompt · 12/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 3f1fb0d6015f: 6 pass, 0 fail, 0 not run
- test · swarmai @ c7ffae2b8d20: 10 pass, 0 fail, 5 not run
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
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_4d014d639e3c/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_platform\_neutral\_jobs.py -q --timeout=60 --maxfail=1000 -p no:cacheprovider → pass (23 passed (incl. the router and port-file test))
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_4d014d639e3c/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_platform\_neutral\_jobs.py tests/test\_feature\_map\_cli.py tests/test\_feature\_map\_sandbo → pass (44 passed)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_4d014d639e3c/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_aws\_account\_consumers.py tests/test\_cdf\_s7\_review.py tests/test\_job\_system\_fixes.py  → pre\_existing (only TestEnsureGitRepo::test\_creates\_initial\_commit and ::test\_commits\_existing\_files fail, identically on the base tree (git commit in a temp repo fails on this machine))
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_4d014d639e3c/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_feature\_map\_replay\_user.py -q --timeout=60 --maxfail=1000 -p no:cacheprovider → pre\_existing (3 tests need setfacl, which this machine lacks; same 3 fail on the base tree)
- python scripts/smoke\_import.py → pass (695 modules)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (no new HIGH)
- python scripts/lint\_skills.py → pass (ok)
- python scripts/lint\_experts.py → pass (ok)
- bash scripts/sync-version.sh check → pass (ok)
- 7 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_4d014d639e3c/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_aws\_account\_consumers.py tests/test\_cdf\_s7\_review.py tests/test\_job\_system\_fixes.py : only TestEnsureGitRepo::test\_creates\_initial\_commit and ::test\_commits\_existing\_files fail, identically on the base tree (git commit in a temp repo fails on this machine)
- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_4d014d639e3c/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_feature\_map\_replay\_user.py -q --timeout=60 --maxfail=1000 -p no:cacheprovider: 3 tests need setfacl, which this machine lacks; same 3 fail on the base tree

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed
- swarmai · npm run lint:contract: no desktop file changed
- swarmai · npm run build: no desktop file changed
- swarmai · npx vitest run --reporter=verbose: no desktop file changed
- swarmai · python -m scripts.feature\_map replay --affected --run run\_4d014d63: all 16 reached walks not\_run: runtime unavailable (the pinned Chromium lacks system libraries here, and this machine has no swarm-replay user); the installed SwarmAI's own feature\_map cannot read this repo's features.yaml schema 2

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_platform\_neutral\_jobs.py
