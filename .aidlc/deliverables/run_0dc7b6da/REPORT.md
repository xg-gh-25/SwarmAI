# Swarm Out 历史页加花费对比，两层：只看模型 / 含机器，按 Knowledge/Mockups/2026-11-23-swarm-out-dashb… · Audit through DELIVER

Delivered: Swarm Out 历史页加花费对比，两层：只看模型 / 含机器，按 Knowledge/Mockups/2026-11-23-swarm-out-dashboard-redesign-v7.html 的花费区实现（用户已认可 v7）。后端开始记机器费：每台 EC2 Hive 的开机/停机区间（开机小时 × 机型时价，hive/fleet.py MONTHLY\_USD），AgentCore 每个任. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 43m 18s wall · 43m 18s active · 20.0% budget |
| Quality | WARN | 1/1 verification · 2 red at base · 3/3 findings closed (4 reported; within 10 lines of one file they count once) (3 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 517k comparable · 396k input · 122k output · 34.1M cache reads, not counted · 34.6M billed · $12.42 book cost · host-session responses in the run window · 39.6% budget |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 3 (+1 found on demand) · searched the DDD 3×

## Decisions

- D1 · Where does an open on period end when the Hive row is gone? → at the last time the Hive was observed (observed\_at of its last row, refreshed on every look); a Hive still in hive\_instances runs to now. Why: a deleted Hive is no longer looked at, and its period must not grow forever (DESIGN\_SPEC, agent)
- D2 · When does a Hive's record begin? → at the since of its first row: an on row's LaunchTime (EC2 says it has run since then), an off row's stop time. Why: uses what EC2 knows without guessing earlier hours (DESIGN\_SPEC, agent)
- D3 · How is AgentCore CPU priced when its use is not measured? → 2 vCPU over the run's active\_s plus 8 GB over the billed session seconds, labelled an estimate. Why: AgentCore bills CPU only while used; active\_s bounds it from above (DESIGN\_SPEC, agent)
- D4 · Which AgentCore session end is used? → the earlier of the released marker's time and the result's ended\_at plus the 900 s idle timeout, capped at opened\_at plus MAX\_RUN\_S. Why: a session not released ends on its own after the idle timeout; a release after that changes nothing (DESIGN\_SPEC, agent)
- D5 · What is a kind of work? → project\_agent.mode plus ' · pipeline' when execution is pipeline; runs without a Project Agent are 'other'. Why: pipeline and native runs of the same mode differ ten times in cost (DESIGN\_SPEC, agent)
- D6 · Where does the table live? → data.db, created by machine\_cost itself (CREATE TABLE IF NOT EXISTS) on first use. Why: additive, no migration of existing tables, testable on a temporary database (DESIGN\_SPEC, agent)
- D7 · Reuse the carrier tag → export Kind from SwarmOutSettings.tsx and reuse it in CostSection. Why: one tag component for EC2 / AgentCore (BUILD report · swarmai, agent)
- D8 · Keep the layer argument of perAccepted → removed: costStats already adds no machine cost in the model layer. Why: a mutation showed the argument changed nothing (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Record on/off from hive\_instances.status changes (a SQLite trigger or each status write). Not taken: status is not EC2's state: it is 'error' while the instance still runs and bills (TECH e08f7322), and starts/stops made outside SwarmAI never reach it
- A3 · Backfill and track with CloudTrail LookupEvents or Cost Explorer. Not taken: needs IAM permissions the Hive account role does not have and a paid or rate-limited service; EC2's own LaunchTime and StateTransitionReason give the transition times on each look
- A4 · Extend fleet.\_ec2\_states to return launch and transition times and record inside reconcile. Not taken: changes the return shape every reconcile test stubs; recording is a separate duty, one extra describe per 10 minutes is negligible

Requirements analysis:

- conflict (R1.1, R3.2): The v7 mock assumed both EC2 Hives were always on; core/hive\_idle stops an idle Hive after 120 minutes by default, so the hours must come from EC2, not from an assumption → rewritten
- missing\_edge (R3.3): No on/off times exist before this change; runs before a Hive's record begins have no known EC2 machine cost → rewritten
- missing\_edge (R2.2): An AgentCore session that was not released ends on its own after the 900 s idle timeout, which is billed → rewritten
- vague (R2.2): AgentCore bills CPU only while it is used; SwarmAI does not measure it, so 2 vCPU over active\_s is an upper bound and is labelled an estimate → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 3/26 tool calls
- correctness · review r1: read 100% of its prompt · 12/26 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ c24c8daf9d38: 8 pass, 0 fail, 0 not run
- test · swarmai @ 0d20a2e1de12: 7 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: pre_existing

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ~ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] flaky: Error: expect(locator).toHaveCount(expected) failed
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/15/0-0-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/15/1-1-trace.zip
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/15/2-2-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✗ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] fail (red at the run's base too): Error: expect(locator).toHaveCount(expected) failed
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/23/0-0-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/23/1-1-trace.zip
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/23/2-2-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/23/3-3-trace.zip
- ✗ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] fail (red at the run's base too): Error: expect(locator).toHaveCount(expected) failed
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/24/0-0-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/24/1-1-trace.zip
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/24/2-2-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/24/3-3-trace.zip
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0dc7b6da/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths the targets ignore: backend/tests/test\_machine\_cost.py, backend/tests/test\_swarm\_out\_live\_dashboard.py, desktop/src/components/swarm-out/CostSection.test.tsx, desktop/src/components/swarm-out/costStats.test.ts


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_machine\_cost.py tests/test\_hive\_fleet.py tests/test\_swarm\_out\_live\_dashboard.py tests/test\_elastic\_dispatch\_e2e.py tests/test\_swarm\_out\_run\_review.py -q --timeout → pass (105 passed)
- python scripts/smoke\_import.py → pass (694 modules import)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- npx tsc --noEmit → pass (clean)
- npm run lint:contract → pass (clean)
- npm run build → pass (built; bundle 4,150 kB under the 4,186 kB cap)
- npx vitest run src/components/swarm-out src/\_\_tests\_\_ → pre\_existing (1128 passed; the 2 failures are layout-viewport-lock.test.ts's RunOverlay and PipelineRunCard title-bar checks, which fail the same way on the run's base 7eb30c337 (another session's overlay commit) i)
- python -m scripts.feature\_map replay --affected --run run\_0dc7b6da → pre\_existing (29 of 31 walks pass. chat-tabs-apart-x7tn fails in both projects on the run's base too (a new tab does not open: 1 tab of 3). chat-tab-draft-nfu9 regular failed once and was flaky on a re-run (compact)

Tests were not run again after main moved: tests carried over a main move: swarmai (main 7eb30c3372fe-&gt;874238d9aa4b): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Red at the run's base too (the agent's report, not checked)

- swarmai · npx vitest run src/components/swarm-out src/\_\_tests\_\_: 1128 passed; the 2 failures are layout-viewport-lock.test.ts's RunOverlay and PipelineRunCard title-bar checks, which fail the same way on the run's base 7eb30c337 (another session's overlay commit) in files this run does not touch
- swarmai · python -m scripts.feature\_map replay --affected --run run\_0dc7b6da: 29 of 31 walks pass. chat-tabs-apart-x7tn fails in both projects on the run's base too (a new tab does not open: 1 tab of 3). chat-tab-draft-nfu9 regular failed once and was flaky on a re-run (compact passes; base passes); it waits for a second tab on a loaded machine. Neither walk reaches code this

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/machine\_cost.py
- swarmai: backend/tests/test\_machine\_cost.py
- swarmai: desktop/src/components/swarm-out/CostSection.test.tsx
- swarmai: desktop/src/components/swarm-out/CostSection.tsx
- swarmai: desktop/src/components/swarm-out/costStats.test.ts
- swarmai: desktop/src/components/swarm-out/costStats.ts
