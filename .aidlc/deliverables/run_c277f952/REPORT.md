# XG 2026-11-23：「为什么 需要我手动点击，现在的Hive EC2, Elastic Hive 都需要手动start。 我不希望这样 应该defa… · Audit through DELIVER

Delivered: XG 2026-11-23：「为什么 需要我手动点击，现在的Hive EC2, Elastic Hive 都需要手动start。 我不希望这样 应该default 都是待命状态 你自己操控 而不是交个人 pending 人」。弹性 Hive 默认待命：Host 有已验证的 Hive 账户且本版本镜像已发布时，自己创建并随版本更新 AgentCore runtime，不用人点开启；人在 Hive 页. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 46m 29s wall · 46m 29s active · 21.5% budget |
| Quality | WARN | 1/1 verification · 1 not run · 10/10 findings closed (11 reported; within 10 lines of one file they count once) (8 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 680k comparable · 525k input · 154k output · 40.8M cache reads, not counted · 41.5M billed · $15.45 book cost · host-session responses in the run window · 34.1% budget |

Project Agent: SwarmAI · code-change · 11 checks · 2 rules · 2 watches

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 1 · searched the DDD 1×

## Decisions

- D1 · How does standby remember the person turned it off? → A config setting hive\_elastic\_standby (DEFAULT\_CONFIG True), set false by the person's DELETE route before the turn-off is claimed and true by the person's POST route. Why: Turning off deletes the row, so the row cannot hold it; one boolean in config needs no table and is readable by the reconcile and the status answer (DESIGN\_SPEC, agent)
- D2 · May the agent call hive.elastic.enable? → No; only hive.elastic.status becomes the owner agent's. Why: Standby makes an agent enable unneeded, and the route cannot tell the person from the agent, so an agent enable would override the person's turn-off (DESIGN\_SPEC, agent)
- D3 · How often is a failure repaired? → Once per daemon start and Host version for each row (an in-process set). Why: A repair that fails again must not create and fail every five minutes; a restart or a new version is a new reason to try (DESIGN\_SPEC, agent)
- D4 · What counts as the image being published? → For a public ECR repository the manifest digest of &lt;repo&gt;:&lt;version&gt; resolves (\_public\_manifest\_digest); a private repository is left to provision's own pin. Why: A Host built from unreleased code has no tag for its version; checking before the claim keeps a failed row from being written every tick (DESIGN\_SPEC, agent)
- D5 · Where does choosing the account live? → Move routers/hive.\_elastic\_target into hive/agentcore\_provisioner.resolve\_target, raising ElasticTargetError(status, detail); the routes map it to HTTPException. Why: The reconcile needs the same choice as the routes, and core code must not import a router (DESIGN\_SPEC, agent)
- D6 · R6.1 planned hive.elastic.status as agent-callable; can it be? → No: GET and POST /api/hive/elastic share a path and the run-owner gate matches paths, not methods, so opening the read would open the person's turn-on. The agent reads the elastic Hive in core/swarm\_out.hives (hive.list, already the agent's): each row's carrier and an elastic\_standby block (on, last round). hive.elastic.status stays the person's. Why: Making the gate method-aware is a security change of its own; the existing list already reaches the agent (BUILD report · swarmai, agent)
- D7 · Keep a once-only guard for updating a ready row? → Deleted. Why: A failed update leaves the row failed (repaired once by account); an update refused before any AWS call is harmless to retry. The mutation check showed the guard decided nothing (BUILD report · swarmai, agent)
- D8 · test\_requests\_are\_signed\_and\_carry\_the\_run\_credential failed once in a batch → Fixed the test: it re-signs at the request's own time. Why: add\_auth takes its own clock and ignores context timestamp, so the test failed whenever a second passed between the two signings (shown with a fixed-clock check); the product's signing is self-consistent (BUILD report · swarmai, agent)
- D9 · Repair key → By account, not row id. Why: Turning on again makes a new row; keyed by row it would create and fail every five minutes (the test caught it) (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Let the agent call hive.elastic.enable and turn it on when a dispatch needs it. Not taken: The person is still the trigger in practice (the agent turns it on at dispatch time, adding a six-minute create to that dispatch), and an agent-callable enable would override the person's own turn-off.
- A3 · Turn the elastic Hive on inside the placement path when no offer exists. Not taken: place() is pure by design (D1/D4) and the dispatch would wait minutes for a create; it also puts an AWS write inside every dispatch.

Requirements analysis:

- missing\_edge (R2.1): a standby reconcile could recreate the elastic Hive right after the person turned it off → rewritten
- missing\_edge (R4.1, R4.2): a failure the Host repairs could be retried on every tick and keep creating and failing → rewritten
- missing\_edge (R1.2): a Host built from unreleased code has no published image for its version; turning on would fail every tick → rewritten
- vague (R6.1): "Hive EC2 needs a manual start" read against the code: a dispatch already starts a stopped EC2 Hive (core/hive\_idle.wake) and hive.start is the owner agent's, so EC2 is unchanged. Agent-called enable is not added: standby makes it unneeded, and it would let an agent override the person's turn-off. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r1: read 100% of its prompt · 13/26 tool calls
- security · review r2: read 100% of its prompt · 10/25 tool calls
- correctness · review r2: read 100% of its prompt · 18/25 tool calls
- security · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 14/25 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r2: read 100% of its prompt · 16/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 1577f1c590d6: 8 pass, 0 fail, 0 not run
- test · swarmai @ fe1a051ed688: 11 pass, 0 fail, 1 not run
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
  - backend-bvt: not_run
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c277f952/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/design/2026-11-20-swarm-out-multi-carrier.md


TEST · swarmai:
- perl -e 'alarm 400; exec @ARGV' .venv/bin/python -m pytest tests/test\_elastic\_hive\_provisioning.py tests/test\_swarm\_out\_ops.py tests/test\_swarm\_out\_host.py tests/test\_app\_config\_manager.py tests/test\_ → pass (451 passed)
- "$SWARM\_BACKEND\_BIN" py -m scripts.mutation\_check per review fix (re-checks, verified gate, SwarmAI mode, skill, polling) → pass (every mutation caught (race2 2/2, verified 1/1, swarmai 1/1, skill 1/1, poll 1/1))
- python scripts/smoke\_import.py → pass (690 modules)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH)
- python scripts/lint\_skills.py → pass (44 skills clean)
- npx tsc --noEmit → pass (no errors)
- npm run lint:contract → pass (clean)
- npm run build → pass (built, bundle under cap)
- npx vitest run src/components/awsAccount src/components/layout/HiveFleetOverlay.test.tsx → pass (63 passed)
- 2 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main 7b5fcb178f31-&gt;a86a9de4b019): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the whole CI set; the suites of every changed module and their callers ran above, CI runs the BVT
