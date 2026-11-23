# Hive provisioner 处理 VPC Block Public Access (BPA)：Amazon 个人 Isengard 账号正在被统一开启 … · Audit through DELIVER

Delivered: Hive provisioner 处理 VPC Block Public Access (BPA)：Amazon 个人 Isengard 账号正在被统一开启 BPA ingress-only 模式（多个 region），开启后 Hive 的 CloudFront 回源入站和经 IGW 的出站都被拦，Hive 全部失联。按 Secure Foundations 规范（优先 subnet 级例外、最小. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 43m 45s wall · 43m 45s active · 20.2% budget |
| Quality | WARN | 1/1 verification · 1 not run · 7/7 findings closed (8 reported; within 10 lines of one file they count once) (4 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 488k comparable · 365k input · 123k output · 23.6M cache reads, not counted · 24.1M billed · $10.10 book cost · host-session responses in the run window · 24.5% budget |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 6 · cited 2 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · One subnet per Hive or one per region? → One per default VPC (region), tagged Name=SwarmAI-Hive-subnet and swarmai:managed=true, /26 (59 addresses, MAX\_HIVES is 8 plus replacements). Why: Exclusions are capped at 50 per account; one shared, small subnet keeps a single exclusion and the smallest scope that still fits every Hive (DESIGN\_SPEC, agent)
- D2 · How does the new instance reach S3 before its Elastic IP is associated? → Set MapPublicIpOnLaunch=true on the Hive subnet, as default subnets have. Why: User data downloads the release at first boot; the Elastic IP is associated after run\_instances returns. Same behavior as today's default subnet (DESIGN\_SPEC, agent)
- D3 · What if BPA cannot be read (older permission template)? → Warn and launch as before (no subnet). Why: Not reading BPA weakens no control: BPA itself is the control, and on an account without BPA the old path is correct. Failing would break accounts the guardrail never touched (DESIGN\_SPEC, agent)
- D4 · How are a deploy and a cleanup in the same region kept from racing on the shared subnet? → A module-level threading lock per region, held inside the launch thread from subnet resolution through run\_instances, and by the release in cleanup. Why: The daemon is one process; the lock makes the gap between ensure and run\_instances unreachable instead of retrying after a NotFound (DESIGN\_SPEC, agent)
- D5 · How long to wait for an exclusion to become active? → Poll every 10 s, give up after 600 s with an error naming the exclusion. Why: A hang guard only: measured create-complete took about 100 s; an exclusion stuck in progress for 10 minutes will not finish (DESIGN\_SPEC, agent)
- D6 · Which AZ for the subnet? → The first AZ (sorted by name) of the default VPC's region where describe\_instance\_type\_offerings offers the Hive's instance type. Why: Some AZs (for example use1-az3) do not offer every type; a fixed AZ would fail there (DESIGN\_SPEC, agent)
- D7 · Release only when the cleaned Hive had an instance? → Always release unused Hive subnets in \_cleanup\_resources. Why: A deploy can fail after creating the subnet and exclusion but before EC2 (an exclusion that never completes); only tagged subnets with no network interface are touched (BUILD report · swarmai, agent)
- D8 · How to treat an unexpected BPA answer (no mode string)? → As unreadable: warn and launch as before. Why: Same as a refused read; a mode string other than off is treated as blocking so a future mode keeps the Hive reachable (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · CloudFront VPC Origins with the Hive in a private subnet. Not taken: AWS's BPA documentation lists CloudFront VPC Origins as blocked (all inbound and outbound traffic), so it does not work under BPA without an exclusion either.
- A3 · Private subnet + NAT gateway for egress and SSM port forwarding (or a reverse tunnel) instead of CloudFront for ingress. Not taken: Needs no exclusion, but replaces how the Host reaches every Hive (CloudFront, origin header lock, Caddy auth), adds a NAT gateway (about 32 USD per month per AZ plus data) and re-pins the role's Elastic IP Deny to the NAT address; the Secure Foundations guidance names CloudFront origins as a legitimate exclusion case, so the cost buys no compliance.
- A4 · Exclude the whole default VPC, or the default subnet the instance lands in. Not taken: The guidance says prefer subnet over VPC and least privilege; a default subnet also hosts anything else the person launches there with a public IP.
- A5 · Record subnet and exclusion ids per Hive in new hive\_instances columns. Not taken: The subnet is shared by every Hive in the region, so a per-Hive column does not decide when it can go; its tags and its network interfaces do. No schema migration is needed.

Requirements analysis:

- missing\_edge (R1.1): An instance in the new subnet boots and downloads its release from S3 before the Elastic IP is associated; without a public IP at launch the first boot has no outbound path. → rewritten
- missing\_edge (R4.1, R1.1): A deploy can race a cleanup that deletes the shared subnet between subnet resolution and run\_instances. → rewritten
- missing\_edge (R2.2): Users on the older permission template cannot read BPA; failing deploy for them would break accounts BPA never touched. → rewritten
- vague (R1.1): Existing Hives in the default subnet are not moved; they move when replaced (a replacement is a new deploy). CloudFront VPC Origins is not used: AWS documents it as blocked by BPA too. → confirmed

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 11/26 tool calls
- security · review r1: read 100% of its prompt · 9/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 17/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 5a2584b692ef: 7 pass, 0 fail, 0 not run
- test · swarmai @ fc787068f3c1: 8 pass, 0 fail, 1 not run
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
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_0301b26b/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png


TEST · swarmai:
- python -m pytest tests/test\_hive\_bpa\_subnet.py tests/test\_aws\_permissions.py tests/test\_hive\_provisioner.py tests/test\_aws\_account\_gaps.py tests/test\_hive.py tests/test\_hive\_hardening.py tests/test\_hi → pass (272 tests: the change's tests and the tests of every caller of launch, cleanup and the capability check; exit 0)
- python -I /tmp/bpa\_e2e/run.py &lt;worktree&gt;/backend (live, account 533267412361 us-east-1, BPA block-ingress) → pass (ensure\_hive\_subnet created SwarmAI-Hive-subnet 172.31.96.0/26 in us-east-1a (MapPublicIpOnLaunch False, managed tags) and an allow-bidirectional subnet exclusion that reached create-complete after 179)
- python -I /tmp/bpa\_e2e/dry.py (live RunInstances DryRun with launch\_placement into the Hive's subnet) → pass (DryRunOperation: the NetworkInterfaces placement (subnet, group, AssociatePublicIpAddress) with the Hive's tags and profile is accepted by EC2)
- python -m scripts.verify\_permission\_template --profile swarm-hive → pass (real IAM: 40/40 as expected)
- python scripts/smoke\_import.py → pass (all modules import)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- python scripts/lint\_problem\_values.py → pass (clean)
- bash scripts/run\_bvt.sh → not\_run (the full BVT is CI's; the change's tests and all their callers' tests ran above)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_0301b26b → pass (31 of 31 walks of the 16 reached features passed (regular and compact))

Tests were not run again after main moved: tests carried over a main move: swarmai (main 2c6daaedfa2d-&gt;99608f68b537): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the full BVT is CI's; the change's tests and all their callers' tests ran above

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/hive/bpa\_subnet.py
- swarmai: backend/tests/test\_hive\_bpa\_subnet.py
