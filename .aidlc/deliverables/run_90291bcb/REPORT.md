# XG 2026-11-23：Swarm Out 只留一个入口，按 Knowledge/Mockups/2026-11-23-swarm-out-single-… · Audit through DELIVER

Delivered: XG 2026-11-23：Swarm Out 只留一个入口，按 Knowledge/Mockups/2026-11-23-swarm-out-single-entry.html（mock v3）实施：「新用户默认仍建 1 台 Hive」「都按 mock v3 定」。要点：左侧导航的 Hive 卡片和 Hive 页删掉，Hive 的状态在 Swarm Out 运行视图「在哪里跑」，每行写明执行面（. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 2h 0m wall · 2h 0m active · 55.5% budget |
| Quality | WARN | 1/1 verification · 1 not run · 9/11 findings closed (15 reported; within 10 lines of one file they count once) (9 test-closed, confirmed by review) · review coverage: security r1 read 16% of its prompt; correctness r1 read 44% of its prompt · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 1.2M comparable · 919k input · 276k output · 103.0M cache reads, not counted · 104.2M billed · $33.47 book cost · host-session responses in the run window · 60.0% budget |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 0 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · May resident Hives go to 0? → Yes while Elastic Workers standby is on: desired\_count may be 0 and the SwarmAI account's last Hive may be deleted; with standby off the at-least-one rule and its refusal stay. New users still start with 1 (XG). Why: mock v3 (XG: 「都按 mock v3 定」) says there can be none; standby is then somewhere to run tasks. Overrides the 1..8 lower bound of the fleet decision (DESIGN\_SPEC, agent)
- D2 · What version does an automatic update go to? → The Host's own version (settings.app\_version), never the latest release. Why: Placement offers a Hive only at a version it can take; following the Host keeps Host and Hives in step, as Elastic Workers already do (DESIGN\_SPEC, agent)
- D3 · When is a Hive updated? → A running Hive with no busy reason (the idle check's own busy\_reasons) is updated by check\_once; a stopped one is updated by wake right after it runs, before the dispatch uses it. One automatic try per Hive and version per process. Why: An update restarts the Hive's backend and ends its sessions; starting a stopped machine only to update it costs money for nothing (DESIGN\_SPEC, agent)
- D4 · How does the person switch Elastic Workers on and off, also with no runtime yet? → One person-only route PUT /api/hive/elastic/standby {on, account\_ref?}: on writes standby true (and the chosen account) and starts a standby round; off writes false and turns off an existing runtime. The settings view and the onboarding step use only it. Why: The DELETE route needs a row, so a new user could not say off before one exists; one switch is one route (DESIGN\_SPEC, agent)
- D5 · Where is the account choice of the old multi-account mode kept? → Config key hive\_elastic\_account, written only by the standby route and excluded from PUT /api/settings like the standby flag; ElasticHive.target uses it when several accounts exist. Why: The standby round needs a stable answer; an agent must not change which account is used (DESIGN\_SPEC, agent)
- D6 · How do other places open the Hive settings now? → A window event (OPEN\_SWARM\_OUT\_SETTINGS) that ChatPage handles by activating the Swarm Out tab and the dashboard's settings view; Settings, Usage, the welcome and the waiting-question links use it. Why: The overlay is gone; an event keeps those callers free of ChatPage's state (DESIGN\_SPEC, agent)
- D7 · How is the one-time note remembered? → localStorage key swarmOut.singleEntryNote.seen, shown only when the fleet already has a Hive. Why: A display preference of this machine; new users have nothing that moved (DESIGN\_SPEC, agent)
- D8 · Where the X15 check (Settings keeps only the account, the Hive row links to Swarm Out settings) is tested → in AwsAccountTab.test.tsx, next to the tab it checks. Why: the plan named the new single-entry test file; the tab's own test file already renders it with its mocks (BUILD report · swarmai, agent)
- D9 · Where the X16 check (onboarding: Elastic Workers on by default, one Hive) is tested → in HiveSetupStep.test.tsx. Why: same reason: the step's own test file renders it (BUILD report · swarmai, agent)
- D10 · Whether a settings row offers restart → yes, as stop then start through the person's routes, enabled only for a running Hive. Why: the agent's restart refuses a Hive that holds a run; without it a hung Hive with a run has no way out but delete. No timer restarts anything (BUILD report · swarmai, agent)
- D11 · How the run view knows a row's execution surface → fleet rows carry carrier (FLEET\_KEYS gains carrier, default ec2). Why: the label must come from the row's executor, never from the name 'elastic' (BUILD report · swarmai, agent)
- D12 · Where the 'cannot build a Hive' line lives after the Hive page is gone → CapabilityLine is exported from HiveFleet.tsx and shown in the settings view when the fleet state is cannot\_build. Why: the person still has to see the missing permission or default VPC next to '+ add one' (BUILD report · swarmai, agent)
- D13 · Take W1 (automatic version updates) as returned from Elastic Workers → taken unchanged. Why: it matches the plan and its key lines are each caught by a mutation. Open for REVIEW: when a stopped Hive behind the Host starts for a dispatch, start plus update must fit in the 270 s dispatch start budget, otherwise that dispatch fails with 'nothing was sent; send again' while the update goes on (BUILD report · swarmai, agent)
- D14 · What to do with the two findings R2 raised (standby off and a row left changing after a quit; two deletes at once lowering the count twice) → fixed in TEST: \_off settles a transient row no task holds before turning it off; the delete decides for its own row (fleet.counts) instead of comparing totals. Why: both are in code this run wrote and the fixes are small; leaving the first would keep a runtime billing the person's account against their off (TEST report · swarmai, agent)
- D15 · Whether start plus update on wake must fit the 270 s dispatch start budget → kept as it is. Why: the budget is the dispatch's existing hang guard; when it runs out the dispatch says nothing was sent and the update goes on, so the next send finds the Hive at the Host's version (TEST report · swarmai, agent)
- D16 · How a delete lowers the wanted Hive count → fleet.delete\_and\_lower: under the fleet lock, the delete's effect is worked out from one read of the counted Hives (with it minus without it), then the row is deleted and desired goes down by that (never below the minimum). Why: per-row decisions read before the long AWS cleanup raced (both halves of a pair), a clamp to what is left dropped a Hive still wanted but not built, and two reads around the delete counted a row another writer changed meanwhile; one snapshot under the lock counts only the deleted Hive (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep the Hive page and only remove the overlap (overview tiles, accounts tab) and the guide. Not taken: XG asked for one entry and no human steps after setup; two pages keep the split and the person still updates by hand.
- A3 · Put the Hive settings in the Settings app tab. Not taken: Settings is the AWS account the model also uses; Hive choices would again sit away from where the work is shown, and mock v3 puts them in Swarm Out.

Requirements analysis:

- conflict (R7.2, R7.3): design 2026-10-31-swarmai-aws-account §7.3 keeps at least one Hive so there is somewhere to dispatch; mock v3 says there can be none and only Elastic Workers. With Elastic Workers on standby there is still somewhere; without it the old rule holds. → rewritten
- conflict (R6.1, R6.3): the Hive E2E design decided 'version updates are controlled by the Desktop owner, never automatic'; XG asked on 2026-11-23 for no human step on updates. Updates follow the Host's version, never the latest release. → rewritten
- missing\_edge (R6.2): an update restarts the Hive's backend, which ends its sessions; only an idle Hive is updated → rewritten
- missing\_edge (R4.2): a new user can switch Elastic Workers off before any runtime exists; turning off must work with no row → rewritten
- missing\_edge (R8.2, R2.4): new person decisions in config must not be writable through the settings route, as the standby flag (routers/settings.\_ELASTIC\_HIVE\_KEYS) → rewritten
- missing\_edge (R5.1): refusals such as routers/swarm\_out.py's standby-off 409 name the Hive page and the elastic Hive → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 7/25 tool calls
- security · review r1: read 16% of its prompt · 17/29 tool calls
- correctness · review r1: read 44% of its prompt · 25/29 tool calls
- correctness · review r2: read 100% of its prompt · 13/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 27d9be654a19: 13 pass, 0 fail, 0 not run
- test · swarmai @ 9e4eaf4a4301: 15 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not run
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_90291bcb/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png


TEST · swarmai:
- .venv/bin/python -m pytest -q -p no:cacheprovider tests/test\_swarm\_out\_single\_entry.py tests/test\_hive\_auto\_update.py tests/test\_hive\_fleet.py tests/test\_elastic\_hive\_provisioning.py tests/test\_swarm\_ → pass (501 passed on the final tree)
- .venv/bin/python -m pytest -q -p no:cacheprovider tests/test\_swarm\_out\_single\_entry.py tests/test\_hive\_fleet.py → pass (34 passed: the delete rule (failed create, short account, replacement pair, deletes at the same time, another row changed during the delete))
- bash scripts/run\_bvt.sh → pass (1334 tests, on the final tree)
- backend/.venv/bin/python scripts/smoke\_import.py → pass (690 modules)
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (clean)
- backend/.venv/bin/python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- backend/.venv/bin/python scripts/lint\_skills.py → pass (44 SKILL.md, 724 corpus files clean)
- npx tsc --noEmit -p . → pass (0 errors)
- npm run lint:contract → pass (clean after adding the product names Elastic Workers and AgentCore Runtime to the reviewed exceptions)
- npm run build → pass (built; bundle 4,097 kB under the 4,186 kB cap)
- 6 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main 2c6daaedfa2d-&gt;fb20fd1de1c3): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Not run (the agent's reason, not checked)

- swarmai · npx vitest run --reporter=verbose: the whole frontend suite is CI's breadth; the scopes above cover every changed component, the removed nav card and overlay (layout, nav contract, visual list), the pages that linked to the Hive page, and tsc compiles every test file

## Hive children (Swarm Run)

- W1 · so\_ee722098918e · elastic · handled · review: take W1's commits into Swarm Run run\_90291bcb; accept: W1 read against the design: an idle running Hive behind the Host version is claimed and updated (claim given back when a dispatch starts during the claim), a stopped one is updated by wake after its s

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_hive\_auto\_update.py
- swarmai: backend/tests/test\_swarm\_out\_single\_entry.py
- swarmai: desktop/src/components/swarm-out/SwarmOutSettings.tsx
- swarmai: desktop/src/components/swarm-out/\_\_tests\_\_/SwarmOutSingleEntry.test.tsx
