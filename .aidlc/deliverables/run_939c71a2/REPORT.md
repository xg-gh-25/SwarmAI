# # Fix E: a failed Hive update can leave the Hive unable to start, and a mid-upd… · Audit through DELIVER

Delivered: # Fix E: a failed Hive update can leave the Hive unable to start, and a mid-update failure is never rolled back The EC2 Hive update script is generated in \`backend/hive/provisioner.py\`. 2.6.0 runs the. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 32m 6s wall · 32m 6s active · no baseline yet (3/5 runs) |
| Quality | WARN | 1/1 verification · 1 red at base · 2/6 findings closed (2 test-closed, confirmed by review) · 1 fail-to-pass · 1 HIGH finding open |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 512k comparable · 382k input · 130k output · 15.7M cache reads, not counted · 16.2M billed · $8.79 book cost · host-session responses in the run window · no baseline yet (3/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 2 (+1 found on demand) · searched the DDD 1×

## Decisions

- D1 · How long to wait for the restart → UPDATE\_START\_WAIT\_S = 90: the unit's 60 s ExecStartPost /health window plus 30 s for the old process to stop and the new one to fork. Why: is-active is false while the unit is deactivating or activating; waiting exactly 60 s would still race a healthy start that used its full window. A test reads the unit's window from hive/swarmai-hive.service and asserts the wait covers it (DESIGN\_SPEC, agent)
- D2 · When to reinstall dependencies on rollback → Only when the failed update's pip step started and backend/pyproject.toml differs between the failed and the restored tree. Why: pip install -e . of an unchanged pyproject.toml installs the same dependency set; a changed one may have upgraded or added packages the restored code was not installed with (DESIGN\_SPEC, agent)
- D3 · Backup into a leftover /opt/swarmai.bak → Backup with rsync -a --delete --exclude=backend/.venv so the backup is an exact copy of the current tree. Why: rsync -a without --delete merges into a backup left by an aborted update, so a rollback would bring back files the current version does not have (DESIGN\_SPEC, agent)
- D4 · Restart on rollback → Restart swarmai-hive in the rollback only when the update had restarted it. Why: A failure before the restart leaves the old process running on the restored tree; restarting it would interrupt live sessions for nothing (DESIGN\_SPEC, agent)
- D5 · How sibling tests find the update's restart now that the rollback function also contains a restart → They look for the top-level line \\nsystemctl restart swarmai-hive --no-block\\n; the rollback's is indented. Why: Their intent is the order of steps relative to the update's own restart (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep the mv swap and move backend/.venv from the failed tree into the restored one before rm -rf of the failed tree. Not taken: Has a window where /opt/swarmai does not exist and a three-step mv chain whose partial failure needs its own recovery; the in-place rsync restore cannot lose the venv by construction and is one command.
- A3 · Include backend/.venv in the backup. Not taken: Copies about 300 MB on every update and still restores a venv that may not match if pip changed it; does not cover the mid-update failure by itself.

Requirements analysis:

- missing\_edge (R1.4): the failed update may have installed a changed systemd unit outside /opt/swarmai; R1.4 makes the rollback reinstall the restored tree's unit when it differs → rewritten
- missing\_edge (R2.1): a backup left by an earlier aborted update would be merged into by rsync -a; the backup now uses --delete so it is an exact copy of the current tree → rewritten
- missing\_edge (R2.1): exits ERR does not see (set -u expansion errors, explicit exit) are covered by an EXIT trap that runs the same rollback while the update is armed; the rollback runs at most once → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 3/25 tool calls
- security · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 4eb42167b8f0: 3 pass, 0 fail, 0 not run
- test · swarmai @ b490a27b65ac: 6 pass, 0 fail, 0 not run
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
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_939c71a2/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_939c71a2/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_939c71a2/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_939c71a2/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_939c71a2/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_939c71a2/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_939c71a2/artifacts/feature-checks/swarmai/6/0-0-app-walk-api-health-q56c.log
- changed paths the targets ignore: backend/tests/test\_hive\_caddy\_binary\_update.py, backend/tests/test\_hive\_caddy\_health\_exemption.py, backend/tests/test\_hive\_provisioner.py, backend/tests/test\_hive\_replay\_host.py, backend/tests/test\_tree\_sitter\_bundle\_setup.py


TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV or die' .venv/bin/python -m pytest tests/test\_hive\_provisioner.py tests/test\_hive\_caddy\_binary\_update.py tests/test\_hive\_replay\_host.py tests/test\_hive\_caddy\_health\_exem → pass (190 passed)
- .venv/bin/python -m pytest tests/test\_hive\_provisioner.py -q --timeout=60 -k TestUpdateScriptRuns -p no:cacheprovider → pass (10 passed)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- bash scripts/run\_bvt.sh → pre\_existing (1476 tests; only test\_p7\_no\_swarmai\_special.py::test\_shipped\_text\_has\_no\_instance\_terms and ::test\_locale\_text\_has\_no\_instance\_terms fail (mwinit in desktop/src/i18n/locales/{en,zh}.json swarmOut.ssh.)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_939c71a2 → pass (7 walks pass)

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 1476 tests; only test\_p7\_no\_swarmai\_special.py::test\_shipped\_text\_has\_no\_instance\_terms and ::test\_locale\_text\_has\_no\_instance\_terms fail (mwinit in desktop/src/i18n/locales/{en,zh}.json swarmOut.ssh.hintPlaceholder, files this run does not touch); both fail the same way on the base tree
