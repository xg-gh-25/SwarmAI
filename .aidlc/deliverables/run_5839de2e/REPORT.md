# SSH 连接瞬时失败自动重试：SSH 机器经 wssh proxy 时约一半连接在握手时超时（'Connection timed out during ban… · Audit through DELIVER

Delivered: SSH 连接瞬时失败自动重试：SSH 机器经 wssh proxy 时约一半连接在握手时超时（'Connection timed out during banner exchange' / 'Connection to UNKNOWN port 65535 timed out'）。今天真机上第一次安装就停在 error 等人点 Retry；续期失败一次下一轮才补。要求：SSH 连接层的瞬时失败（连. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 59m wall · 1h 59m active · 21.3% budget |
| Quality | WARN | 1/1 verification · 7 not run · 6/6 findings closed (9 reported; within 10 lines of one file they count once) (5 test-closed, confirmed by review) · 1 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | WARN | 961k comparable · 752k input · 209k output · 84.8M cache reads, not counted · 85.7M billed · host-session responses in the run window · 109.6% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 4 · design recalled · 7 · cited 1 · searched the DDD 1×

## Decisions

- D1 · what counts as connection-phase → exit code 255 and a stderr line in ssh's own forms: 'Connection timed out during banner exchange', 'Connection to &lt;host&gt; port &lt;n&gt; timed out', 'kex\_exchange\_identification: ...', 'ssh: connect to host &lt;host&gt; port &lt;n&gt;: Connection refused\|Operation timed out\|Connection timed out\|No route to host'. Why: ssh exits 255 for its own errors and prints these before authentication, so nothing ran; a remote command's own 'connection refused' in another form, or a drop mid-command, is not matched (DESIGN\_SPEC, agent)
- D2 · how many tries and how far apart → 3 attempts, 3 seconds apart (CONNECT\_TRIES, CONNECT\_RETRY\_S). Why: today's failures were single; three attempts cover a flap without hiding a machine that is down (the error comes after about 50 seconds with ConnectTimeout 15) (DESIGN\_SPEC, agent)
- D3 · the profile check's own loop → \_retry\_identity calls \_run with tries=1. Why: \_check\_identity already loops 6 times; one attempt per loop keeps it at 6 connections (DESIGN\_SPEC, agent)
- D4 · whether \_ssh\_hint reads the new classifier (design A1) → no: \_ssh\_hint is unchanged. Why: it words hints for a wider set (including 'did not finish within' and the person's own fixes), and its unreachable hint already covers every connection-phase line; changing it would change hints for no gain (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Add ConnectionAttempts=3 to the ssh options. Not taken: ConnectionAttempts retries the TCP connect only; a ProxyCommand that times out in the banner exchange (the failure seen) is not retried by it.
- A3 · Retry every failed step a few times. Not taken: A command that ran on the machine and failed (or half ran: a swap) must not run again; auth and host-key failures need the person, retrying them only delays the hint.

Requirements analysis:

- missing\_edge (R1.1): A retry is safe only when the command never ran: ssh exits 255 for its own errors and the listed messages all come before authentication, so nothing ran on the machine. A connection that drops mid-command ('Connection closed', 'Broken pipe') may have run part of a script and is deliberately not in the list. → rewritten
- missing\_edge (R1.1, R2.1): One classifier of connection-phase messages serves both the provisioner and the tunnel, so the two never disagree about what is transient. → confirmed
- missing\_edge (R1.2): The renewal pass already retries every round; the step-level retry makes a single flaky connection invisible there too, and a machine that stays unreachable still records renew\_error. → confirmed
- missing\_edge (R1.1, R1.4): A remote command can exit 255 and print words like 'connection refused' (curl inside a script); only ssh's own message forms count, so such a command is not run again. A test runs a remote 'exit 255' with that text. → rewritten
- missing\_edge (R1.1): \_check\_identity already loops 6 times over the profile check; it calls \_run with one attempt, so its loop is not multiplied by the connection retry (6 checks, not 18 connections). → rewritten
- missing\_edge (R1.5): 'did not finish within' (a step killed at its timeout) stays outside the classifier; \_ssh\_hint keeps wording it for the person. → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- security · review r2: read 100% of its prompt · 14/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 761bf0864677: 3 pass, 0 fail, 0 not run
- test · swarmai @ c31b8122951a: 6 pass, 0 fail, 7 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5839de2e/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5839de2e/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5839de2e/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5839de2e/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5839de2e/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5839de2e/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5839de2e/artifacts/feature-checks/swarmai/6/0-0-app-walk-api-health-q56c.log
- changed paths the targets ignore: backend/tests/test\_ssh\_connect\_retry.py, backend/tests/test\_ssh\_machine\_autoconfig.py, backend/tests/test\_ssh\_preflight\_hints.py, backend/tests/test\_ssh\_provisioner.py


TEST · swarmai:
- perl -e 'alarm 280; exec @ARGV' .venv/bin/python -m pytest -q -p no:cacheprovider tests/test\_ssh\_connect\_retry.py tests/test\_ssh\_provisioner.py tests/test\_ssh\_identity.py tests/test\_ssh\_machine\_autoco → pass (76 passed on the tree rebased onto main (which changed core/carriers/ssh.py for the Settings run), Settings route tests included)
- python scripts/smoke\_import.py → pass (711 modules import)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- bash scripts/run\_bvt.sh → pass (1462 passed on the rebased tree (main's library\_mounts change cleared the earlier silent-except baseline failure))
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_5839de2e → pass (7 walks passed, 0 failed)
- npx tsc --noEmit → not\_run (no frontend file changed)
- npm run lint:contract → not\_run (no frontend file changed)
- npm run build → not\_run (no frontend file changed)
- npx vitest run --reporter=verbose → not\_run (no frontend file changed)
- 3 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no frontend file changed
- swarmai · npm run lint:contract: no frontend file changed
- swarmai · npm run build: no frontend file changed
- swarmai · npx vitest run --reporter=verbose: no frontend file changed
- swarmai · python scripts/lint\_skills.py: no skill changed
- swarmai · python scripts/lint\_experts.py: no expert prompt changed
- swarmai · bash scripts/sync-version.sh check: no version touched

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_ssh\_connect\_retry.py
