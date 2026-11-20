# 实现已评审定稿的 Swarm Out 多执行面设计 docs/design/2026-11-20-swarm-out-multi-carrier.md（D1–… · Audit through DELIVER

Delivered: 实现已评审定稿的 Swarm Out 多执行面设计 docs/design/2026-11-20-swarm-out-multi-carrier.md（D1–D9 已由 XG 2026-11-20 全部定下，AgentCore spike 实测结果在 §6.1）：派发层（执行面、位置、run 句柄、确定性调度）、抽出 EC2 执行面且行为不变、AgentCore 执行面（Hive 侧 AgentC. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | WARN | 3h 36m wall · 3h 36m active · 141.8% budget |
| Quality | WARN | 1/1 verification · 4/6 findings closed (3 test-closed, confirmed by review) · review coverage: security r1 read 6% of its prompt; correctness r1 read 24% of its prompt |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 918k comparable · 716k input · 203k output · 76.4M cache reads, not counted · 77.3M billed · $25.06 book cost · host-session responses in the run window · 46.1% budget |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 6 · searched the DDD 3×

## Decisions

- D1 · How does the image reach the user's account? → The release workflow builds the ARM64 image from the Hive package and pushes it to a public ECR repository; the runtime's containerUri points at it, set by the config key hive\_agentcore\_image; with no image configured the elastic Hive cannot be turned on and the Hive page says why. Why: CreateAgentRuntime accepts a public ECR image (CloudTrail shows a runtime created on 2026-10-12 from public.ecr.aws in this account); no ECR repository or Docker is needed on the user's side, which every user must be able to do (SOUL P7). A build in the user's account (CodeBuild) stays possible behind the same key (DESIGN\_SPEC, agent)
- D2 · Where do the run handles live? → In the dispatch row as rounds: \[handle, ...\]; credentials stay out of the row (auth\_ref only). Why: Design §4.3; v0 G4 forbids credentials in dispatch rows (DESIGN\_SPEC, agent)
- D3 · Which failures fail over to another location? → Only LocationUnavailable raised before any write to the location, within the remaining shared deadline; never after a write, never after a failed start. Why: Design §4.4 step 3, after the premise review found writes left on the first Hive and a 480 s shared deadline (DESIGN\_SPEC, agent)
- D4 · How is the per-call-site migration enforced? → A test scans backend/core, backend/routers and backend/jobs with a word-boundary regex for \_instance( and \_instance\_up( outside backend/core/carriers. Why: Structural: a missed call site fails CI instead of booting an empty microVM in production (DESIGN\_SPEC, agent)
- D5 · How does BUILD run? → As a Swarm Run: the lead writes the contract (T0) in the run worktree, then dispatches W1-W7 as Hive children with base @worktree and their after dependencies; the lead merges, applies and finishes BUILD. Why: XG asked for it; AGENT § Iterative Refinement: parts that wait for nothing run at the same time on the person's Hives (DESIGN\_SPEC, agent)
- D6 · How is AgentCore mode detected on the Hive? → The environment variable SWARMAI\_CARRIER=agentcore set by the supervisor when it starts the backend; SWARMAI\_MODE stays hive. Why: Every Hive behaviour keeps working; AgentCore-only behaviour is gated on one explicit value (DESIGN\_SPEC, agent)
- D7 · What is the busy state's source of truth? → A backend endpoint GET /api/hive/busy computes the four conditions; the supervisor's /ping reads it. Why: The backend owns sessions, waits, review jobs and pending S3 writes; the supervisor must not reimplement them (DESIGN\_SPEC, agent)
- D8 · Default runtime size → resourceConfigurations minimumMemoryInMiB 8192 on create and on every update. Why: XG 2026-11-20; measured 27 GB free disk and 8 vCPU with SwarmAI fully installed in 5.2 GB (DESIGN\_SPEC, agent)

## Findings

Considered and not taken:

- A2 · A cloud control plane in the user's account (API Gateway, Lambda, Step Functions, DynamoDB) that queues and places runs. Not taken: D1 decided against it: no real need for queueing or several Hosts today, it adds a resident system to operate, and it conflicts with the Host being the command centre; place() stays a pure function so it can move there later.
- A3 · Branch on carrier in place at each of the 22 \_instance call sites and in pick\_hive. Not taken: Every new carrier repeats the branch at every call site; a missed one only shows in production (a request to a stopped AgentCore session boots an empty microVM). The design review rejected it.

Requirements analysis:

- missing\_edge (R6.1): The design leaves open how the image reaches the user's ECR (Q2): a product-published image copied in, or a build in the user's account. Every user must be able to turn it on without Docker on their machine (SOUL P7). THINK decides the mechanism; R6.1 stays about the runtime shape. → rewritten
- missing\_edge (R1.6): A start of a stopped Hive keeps running in the background after it failed, so it is never followed by another candidate within the same 480 s deadline. → rewritten
- missing\_edge (R2.2): A naive \_instance( match also hits create\_instance( and similar names; the check uses a word-boundary regex. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 6% of its prompt · 23/33 tool calls
- correctness · review r1: read 24% of its prompt · 24/33 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r2: read 100% of its prompt · 13/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ c59a59122787: 5 pass, 0 fail, 0 not run
- test · swarmai @ ae5993b9f0ee: 8 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: not run
  - version-check: pass
  - backend-bvt: not run
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- – first-run-setup-q8rx: feature check not run on this tree
- – pipeline-view-vmna: feature check not run on this tree
- – todo-view-55s5: feature check not run on this tree
- – jobs-view-k98s: feature check not run on this tree
- – settings-view-ze9a: feature check not run on this tree
- – brain-hub-bvcc: feature check not run on this tree
- – capabilities-view-6qa4: feature check not run on this tree
- – chat-tab-draft-nfu9: feature check not run on this tree
- – no-account-banner-6atk: feature check not run on this tree
- – api-health-q56c: feature check not run on this tree
- – chat-send-stream-h4kw: feature check not run on this tree
- – chat-tool-card-m2rp: feature check not run on this tree
- – chat-tabs-apart-x7tn: feature check not run on this tree
- – chat-stop-p9ds: feature check not run on this tree
- – chat-model-error-b3qz: feature check not run on this tree
- – chat-thinking-t5wq: feature check not run on this tree
- changed paths under no target: .github/workflows/release.yml, hive/agentcore/Dockerfile, hive/agentcore/build\_image.sh, hive/agentcore/supervisor.py


TEST · swarmai:
- SWARMAI\_SUITE=1 perl -e 'alarm 500; exec @ARGV' .venv/bin/python -m pytest tests/test\_carriers\_placement.py tests/test\_carriers\_ec2.py tests/test\_carriers\_no\_instance\_outside.py tests/test\_swarm\_out\_r → pass (688 passed on the tree rebased onto main 73e91f074)
- python scripts/smoke\_import.py → pass (693 modules import (rebased tree))
- python scripts/lint\_problem\_values.py → pass (1947 files keep their loaders' problem values (rebased tree))
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS (rebased tree))
- bash scripts/sync-version.sh check → pass (all versions 2.5.5 (rebased tree))
- npx tsc --noEmit → pass (clean (rebased tree))
- npm run lint:contract → pass (clean (rebased tree))
- npx vitest run src/components/awsAccount/\_\_tests\_\_/ElasticHiveSection.test.tsx src/components/layout/HiveFleetOverlay.test.tsx → pass (30 passed: the Hive page's Elastic Hive section (rebased tree))

## Hive children (Swarm Run)

- W3 · so\_5d8dae0bf06a · xg-swarm-remote-explorer · handled · review: take W3's commits into Swarm Run run\_fd94fed6; accept: AgentCore mode gated on SWARMAI\_CARRIER=agentcore: X-Swarm-Hive-Auth credential, bridge identity from env with a lazy S3 client and no IMDS, record entries plus state/checks/usage exported to S3 with 
- W2 · so\_9b229cb3d269 · xg-swarm-remote-explorer · handled · review: take W2's commits into Swarm Run run\_fd94fed6; accept: place() and open\_with\_failover() implemented; the lead's 7 contract tests pass unchanged plus 9 added (16 passed); reviewed: place is pure, D5 holds (cold only when running Hives are saturated or none
- W1 · so\_6a471fd8b97f · xg-swarm-remote-explorer · handled · review: take W1's commits into Swarm Run run\_fd94fed6; accept: Ec2Carrier/Ec2Location and registry wrap today's swarm\_out code with no behaviour change; reviewed offers (other-account Hives left out, blocked reasons, stopped-Hive probe memory) and open\_run (Locat
- W4 · so\_8efa11226359 · xg-swarm-remote-explorer · handled · review: take W4's commits into Swarm Run run\_fd94fed6; rerun: the merge refuses CI files from a child: the lead took the release.yml job itself; W4 drops it; review: take W4's commits into Swarm Run run\_fd94fed6; accept: supervisor (started marker with IfNoneMatch, replaced refusal, HealthyBusy rule, 0.0.0.0:8081 backend with the carrier env), Dockerfile (AL2023, EXPOSE 8080 8081) and build\_image.sh landed without the
- W5 · so\_4ea9653c9d8f · xg-swarm-remote-demo · handled · review: take W5's commits into Swarm Run run\_fd94fed6; accept: AgentCoreCarrier/Location: run credential 0600 on this machine only, SigV4 client on the 8081 port URL with X-Swarm-Hive-Auth that refuses before sending once the round ended, results only from S3 in 
- W6 · so\_35b3b10620b2 · xg-swarm-remote-demo · handled · review: take W6's commits into Swarm Run run\_fd94fed6; accept: every per-run caller resolves its round through registry.handle\_of / client\_for and the carrier, per-machine work through locations and carrier duties, placement and failover in dispatch with the refu
- W7 · so\_8dd465ef08bc · xg-swarm-remote-demo · handled · rerun: the merge refuses backend/uv.lock (matches \*.lock on the Host): the lead took the lock change itself; W7 drops it; review: take W7's commits into Swarm Run run\_fd94fed6; accept: child merge refused because its review base is the run's base, so the lead's own release.yml change in the child-base snapshot counted as the child's; the lead took W7's own commits (1bd8114bf..d81fa1
- W8 · so\_07d305cdbbba · xg-swarm-remote-explorer · open

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/carriers/\_\_init\_\_.py
- swarmai: backend/core/carriers/agentcore.py
- swarmai: backend/core/carriers/base.py
- swarmai: backend/core/carriers/ec2.py
- swarmai: backend/core/carriers/placement.py
- swarmai: backend/core/carriers/registry.py
- swarmai: backend/core/hive\_agentcore.py
- swarmai: backend/hive/agentcore\_provisioner.py
- swarmai: backend/tests/test\_agentcore\_supervisor.py
- swarmai: backend/tests/test\_carriers\_agentcore.py
- swarmai: backend/tests/test\_carriers\_dispatch\_layer.py
- swarmai: backend/tests/test\_carriers\_ec2.py
- swarmai: backend/tests/test\_carriers\_no\_instance\_outside.py
- swarmai: backend/tests/test\_carriers\_placement.py
- swarmai: backend/tests/test\_carriers\_placement\_w2.py
- swarmai: backend/tests/test\_elastic\_dispatch\_e2e.py
- swarmai: backend/tests/test\_elastic\_hive\_provisioning.py
- swarmai: backend/tests/test\_hive\_agentcore\_mode.py
- swarmai: desktop/src/components/awsAccount/\_\_tests\_\_/ElasticHiveSection.test.tsx
- swarmai: hive/agentcore/Dockerfile
- swarmai: 2 more in run_audit.json
