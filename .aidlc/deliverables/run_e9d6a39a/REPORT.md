# ## Goal: a stranger who sets up AWS with the product's IAM template can run Ela… · Audit through DELIVER

Delivered: ## Goal: a stranger who sets up AWS with the product's IAM template can run Elastic Workers backend/core/aws\_permissions.py \~:41-73 lists no \`bedrock-agentcore:\*\`, no \`logs:\*\`, no ECR. IAM resources a. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 35m 3s wall · 35m 3s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 4 not run · 5/8 findings closed (9 reported; within 10 lines of one file they count once) (4 test-closed, confirmed by review) · 1 fail-to-pass · CF-146fe068: no mutation check (a name added to the unresolved-variable guard: it fires only if a later edit stops supplying the value, which no path does today; test\_the\_first\_boot\_wait\_cover) · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 752k comparable · 578k input · 174k output · 34.9M cache reads, not counted · 35.6M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 4 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which resource can CreateAgentRuntime be scoped to? → Resource "\*" with aws:RequestTag/swarmai:managed=true (the create call always carries it); every other runtime action on runtime/swarmai\_elastic\_hive-\*. Why: AWS's Service Authorization Reference lists no resource type for CreateAgentRuntime (only request-tag conditions), so a runtime ARN would never match (DESIGN\_SPEC, agent)
- D2 · May the template allow iam:UpdateAssumeRolePolicy now? → Only on role/SwarmAI-ElasticHive-\*, never on SwarmAI-Hive-\*. Why: the adopt path rewrites the Elastic role's trust; that role is created and refreshed only with the SwarmAI boundary, and CreateRole on the same prefix already lets the caller choose a trust policy, so this adds no reach; IAM has no condition key on the trust document (DESIGN\_SPEC, agent)
- D3 · What does Elastic do when the permission simulation itself cannot run? → Proceed as before (the steps' own errors say what failed) and log it. Why: an identity allowed everything but iam:SimulatePrincipalPolicy must not be blocked; refusing on 'could not tell' would be a false block (DESIGN\_SPEC, agent)
- D4 · How does Elastic's status show the gap without an AWS call per read? → The last check's gap per (account id, region) is kept in memory and shown as the status reason; the standby round (every 300 s) and each turn-on re-check, so a fixed permission clears it. Why: status is read by the settings view repeatedly; simulation costs several IAM calls (DESIGN\_SPEC, agent)
- D5 · Should the first-boot script's output reach the console? → Yes: tee the setup log to stdout (cloud-init writes it to the console). Why: without it the console holds only cloud-init lines and does not show which setup step hung, so the copied console output could not diagnose the timeout (DESIGN\_SPEC, agent)
- D6 · Which DescribeLogGroups / ListAgentRuntimes / ListServiceQuotas resource? → "\*". Why: none of them has a resource type in the Service Authorization Reference; FilterLogEvents and DescribeLogStreams are scoped to log-group:/aws/bedrock-agentcore/\* (DESIGN\_SPEC, agent)
- D7 · The guard's EC2 module list → also scans hive/fleet.py, hive/accounts.py and core/hive\_diagnostics.py (all covered already); routers/hive.py's legacy verify probe stays out, by name with the reason. Why: Gate-0 finding: the list was hand-picked (BUILD report · swarmai, agent)
- D8 · Policy size → a test keeps the template within an inline policy's 10,240 characters (8,901 now). Why: the base template already exceeded a customer managed policy's 6,144; the additions must not push it past the inline limit (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A separate IAM template and boundary only for Elastic Workers. Not taken: two templates for one identity drift apart and the person must apply both; the existing single list is the source of the template, the checks and the guard test
- A3 · Probe Elastic readiness with real calls (create a throwaway runtime / DryRun). Not taken: AgentCore has no DryRun; a probe would create billed resources, and the existing checks already use policy simulation for actions without DryRun
- A4 · Raise the EC2 wait to a fixed 900 without deriving it. Not taken: a later deadline increase would silently make the wait too short again; deriving it from the user\_data constants and pinning it with a test keeps it a hang guard

Requirements analysis:

- missing\_edge (R1.4): \_ensure\_role creates the Elastic role with SwarmAI-Hive-Boundary when the account has it (template users do); the boundary has no logs or ECR, so a template user's runtime could not write its logs or pull a private image → rewritten
- missing\_edge (R1.1): AWS's Service Authorization Reference lists dependent actions for CreateAgentRuntime and DeleteAgentRuntime that the code never names → rewritten
- missing\_edge (R4.2): the console output can be large and holds cloud-init lines; only the tail is kept, and the read happens before cleanup → rewritten
- missing\_edge (R4.3): user-data redirects all output to the log file, so the console would not show which setup step hung → rewritten
- missing\_edge (R2.4): an identity allowed everything but SimulatePrincipalPolicy must not be blocked → rewritten
- missing\_edge (R3.2): Gate-0: the guard's module list was hand-picked; fleet.py, accounts.py and hive\_diagnostics.py call AWS too (all covered today), routers/hive.py probes a legacy account → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 7/25 tool calls
- security · review r1: read 100% of its prompt · 5/26 tool calls
- correctness · review r1: read 100% of its prompt · 15/26 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 12/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 4cfb1d2f4f5e: 5 pass, 0 fail, 0 not run
- test · swarmai @ 5f5c97c3dcea: 9 pass, 0 fail, 4 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: not_run
  - version-check: pass
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- python -m pytest tests/test\_aws\_permissions.py tests/test\_elastic\_hive\_provisioning.py tests/test\_hive\_hardening.py tests/test\_hive\_provisioner.py tests/test\_hive\_fleet.py tests/test\_swarm\_out\_host.py → pass (474 passed, 7 skipped: the change's tests and its callers' tests)
- python scripts/smoke\_import.py → pass (697 modules import)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- python scripts/lint\_skills.py → pass (44 SKILL.md pass)
- python scripts/lint\_experts.py → pass (10 expert files pass)
- npx tsc --noEmit → pass (AwsAccountView type gained an optional elastic verdict)
- npm run lint:contract → pass (no hard-coded UI text added)
- bash scripts/sync-version.sh check → pass (2.5.5 everywhere)
- npm run build → not\_run (a type-only frontend change (tsc passed); run through task\_checks on the landed branch)
- 3 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npm run build: a type-only frontend change (tsc passed); run through task\_checks on the landed branch
- swarmai · npx vitest run --reporter=verbose: no component changed; run through task\_checks on the landed branch
- swarmai · bash scripts/run\_bvt.sh: run through task\_checks on the landed branch
- swarmai · python -m scripts.feature\_map replay --affected --run run\_e9d6a39a: environment: all 16 reached walks not\_run (this machine has no Chromium system libraries and no swarm-replay user; the installed SwarmAI reads features.yaml schema 1, the repo's is 2)
