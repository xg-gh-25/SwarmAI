# 修 SSH 证书提示和隧道失败状态 — (1) a probe of a running SSH machine whose tunnel build las… · Audit through DELIVER

Delivered: 修 SSH 证书提示和隧道失败状态 — (1) a probe of a running SSH machine whose tunnel build last failed reports it unreachable with ssh's error, not 'tunnel still starting' (core/carriers/ssh.\_admission + ssh\_tunnel). Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 32m 41s wall · 32m 41s active · no baseline yet (2/5 runs) |
| Quality | WARN | 1/1 verification · 3/5 findings closed (6 reported; within 10 lines of one file they count once) (2 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 404k comparable · 318k input · 86k output · 30.4M cache reads, not counted · 30.8M billed · $10.34 book cost · host-session responses in the run window · no baseline yet (2/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 0

## Decisions

- D1 · a Host-level setting for the renew command, or per machine → per machine (login\_renew\_hint). Why: no new settings store; it uses the existing options route and validation; each machine names how its login renews (BUILD report · swarmai, agent)
- D2 · name the renew command (mwinit) in the product → no. Why: works for every user and company (P7); the person sets the command once, the row offers the form (BUILD report · swarmai, agent)

## Findings

Requirements analysis:

- missing\_edge (R4.1): a computer can hold an unrelated expired certificate while the machine uses plain keys: the expired note shows only while the machine is also unreachable → rewritten
- missing\_edge (R3.1): the renew command is per machine, not a Host setting: no new settings store; each machine names how its login renews → confirmed
- missing\_edge (R1.1): a probe after a failure still starts a rebuild, as today; the probe cadence is unchanged → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 12/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/26 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r1: read 100% of its prompt · 1/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ d1f8f1306404: 5 pass, 0 fail, 0 not run
- test · swarmai @ e4804a071c92: 8 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_5b84606d/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths the targets ignore: backend/tests/test\_ssh\_cert\_and\_tunnel\_state.py, desktop/src/components/swarm-out/\_\_tests\_\_/SshHostCert.test.tsx, desktop/src/services/hive.ssh.test.ts


TEST · swarmai:
- backend/.venv/bin/python scripts/smoke\_import.py → pass (712 modules import)
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (problem values kept)
- backend/.venv/bin/python scripts/security\_scan.py → pass (no new HIGH, secrets or wildcard CORS)
- npx tsc --noEmit → pass (no type errors)
- npm run lint:contract → pass (contract lint clean)
- .venv/bin/python -m pytest -q -p no:cacheprovider --timeout=60 tests/test\_ssh\_cert\_and\_tunnel\_state.py tests/test\_ssh\_tunnel.py tests/test\_ssh\_provisioner.py tests/test\_hive\_ssh\_routes.py tests/test\_s → pass (the change's tests and the SSH tunnel, provisioner, routes and carrier tests)
- npx vitest run src/components/swarm-out src/components/settings src/services/hive.ssh.test.ts → pass (412 tests, incl. a renewal started more than the watch window ago is no longer shown as started)
- $SWARM\_BACKEND\_BIN py -m scripts.feature\_map replay --affected --run run\_5b84606d → pass (16 reached features, 31 walks pass)

Tests were not run again after main moved: tests carried over a main move: swarmai (main 57373aa73753-&gt;98176fa5556f): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_ssh\_cert\_and\_tunnel\_state.py
- swarmai: desktop/src/components/swarm-out/\_\_tests\_\_/SshHostCert.test.tsx
- swarmai: desktop/src/services/hive.ssh.test.ts
