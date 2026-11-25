# Settings tab 秒开：Swarm Out dashboard 的 Settings tab 加载体验差。根因（已定位）：(1) SwarmOutSe… · Audit through DELIVER

Delivered: Settings tab 秒开：Swarm Out dashboard 的 Settings tab 加载体验差。根因（已定位）：(1) SwarmOutSettings.tsx 的 load 用一个 Promise.allSettled 等 5 个请求全回来才显示，最慢的 /hive/elastic 每次现场调 AWS GetAgentRuntime；(2) /hive/ssh 对每台运行中的. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 12m wall · 1h 12m active · 33.5% budget |
| Quality | WARN | 1/1 verification · 2 not run · 2 red at base · 5/5 findings closed (4 test-closed, confirmed by review) · a requirement not covered by the design · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 556k comparable · 420k input · 136k output · 67.6M cache reads, not counted · 68.1M billed · host-session responses in the run window · 42.6% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 0 · searched the DDD 1×

## Decisions

- D1 · how R4 (Elastic without an AWS call per read) is met → on the client: the Elastic card no longer holds the tab, says Loading until first read, and a reopened tab shows the last status at once while the read refreshes underneath; the route keeps its live read, so the transient settle of R4.3 is unchanged. Why: the cost the person saw was the whole tab waiting; a server cache would add invalidation to four write paths for a sub-second first paint (DESIGN\_SPEC, agent)
- D2 · how fresh the SSH list's reachability is → the last admission probe of the machine, from any caller (the fleet polls every 30 seconds); a probe older than 30 seconds, or none, starts one in the background (one per machine at a time) and the section polls until it is known. Why: the fleet already probes each machine; the list stops probing it a second time and never waits on a stalled tunnel (DESIGN\_SPEC, agent)
- D3 · where the tab's last data lives between openings → a module-level copy in SwarmOutSettings.tsx that the state starts from and every successful read writes; key={view} stays (it replays the view's entry animation). Why: the smallest change that survives the remount; nothing else reads it (DESIGN\_SPEC, agent)

## Findings

Considered and not taken:

- A2 · A1 plus a server-side cache of GetAgentRuntime with a TTL behind /hive/elastic. Not taken: Only the Elastic card waits on it once the tab no longer waits for all five requests, and a reopened tab shows the last answer at once; a server cache needs invalidation on every enable, upgrade, reconcile and disable (the status tests read the runtime right after each) for a sub-second gain on the first open.
- A3 · Keep Promise.allSettled and add per-request timeouts. Not taken: The tab would still wait for the slowest request up to the timeout, and a timeout hides the slow probe instead of moving it off the path.

Requirements analysis:

- missing\_edge (R2.1): An action on the tab (delete, retry, a switch) still reloads its data and shows the fresh result; the shared copy only removes the empty first paint. → rewritten
- missing\_edge (R3.1, R3.2): The fleet poll already probes each SSH machine every 30 seconds; its result is the one the list shows, so the machine is not probed twice for the same answer. → confirmed
- missing\_edge (R4.3): A transient row needs the live read to settle and the tab polls it every 10 seconds while it changes; that rare case keeps its AWS read. → confirmed

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 1e4bba3b76e0: 4 pass, 0 fail, 0 not run
- test · swarmai @ 29a3439f8be2: 8 pass, 0 fail, 2 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not_run
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_8978cb38/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths the targets ignore: backend/tests/test\_settings\_fast\_routes.py, desktop/src/components/swarm-out/\_\_tests\_\_/SettingsLoading.test.tsx, desktop/src/components/swarm-out/\_\_tests\_\_/SshMachines.test.tsx, desktop/src/components/swarm-out/\_\_tests\_\_/SwarmOutIdlePerHive.test.tsx, desktop/src/components/swarm-out/\_\_tests\_\_/SwarmOutSingleEntry.test.tsx


TEST · swarmai:
- perl -e 'alarm 280; exec @ARGV' .venv/bin/python -m pytest -q -p no:cacheprovider tests/test\_settings\_fast\_routes.py tests/test\_ssh\_carrier\_offers.py tests/test\_ssh\_carrier\_rounds.py tests/test\_ssh\_ca → pass (22 passed after the review fixes)
- npx vitest run src/components/swarm-out → pre\_existing (319 passed, 1 failed: funnel.test.tsx 'a run landed by hand says who landed it' fails the same way on the base (checked earlier today on a base tree); every Settings test passes, the new SettingsLoadi)
- python scripts/smoke\_import.py → pass (imports)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new findings)
- npx tsc --noEmit → pass (clean)
- npm run lint:contract → pass (clean)
- npm run build → pass (within the bundle caps)
- bash scripts/run\_bvt.sh → pre\_existing (1435 passed, 2 failed: tests/test\_silent\_except\_baseline.py counts 5 silent broad excepts in core/library\_mounts.py against a baseline of 4; that file is unchanged by this run and its last change (c06)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_8978cb38 → pass (31 walks passed)
- 2 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main a54df903bdce-&gt;6edd22de8ad0): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Red at the run's base too (the agent's report, not checked)

- swarmai · npx vitest run src/components/swarm-out: 319 passed, 1 failed: funnel.test.tsx 'a run landed by hand says who landed it' fails the same way on the base (checked earlier today on a base tree); every Settings test passes, the new SettingsLoading.test.tsx 6/6
- swarmai · bash scripts/run\_bvt.sh: 1435 passed, 2 failed: tests/test\_silent\_except\_baseline.py counts 5 silent broad excepts in core/library\_mounts.py against a baseline of 4; that file is unchanged by this run and its last change (c06095ee6, another session) is in the run's base. A first run under load timed out once in a TestClient

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/sync-version.sh check: no version touched
- swarmai · npx vitest run --reporter=verbose: the full frontend suite: the change reaches only Swarm Out, run above

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_settings\_fast\_routes.py
- swarmai: desktop/src/components/swarm-out/\_\_tests\_\_/SettingsLoading.test.tsx
