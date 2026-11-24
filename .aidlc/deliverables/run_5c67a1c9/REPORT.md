# ## Goal: every AWS call goes through the product's AWS account layer, and its g… · Audit through DELIVER

Delivered: ## Goal: every AWS call goes through the product's AWS account layer, and its guard runs in CI backend/tests/test\_aws\_account\_integrity.py is red on main and not in the BVT set, so CI never sees it. F. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 26m 40s wall · 26m 40s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 2 not run · 2 red at base · 1/3 findings closed · 2 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 356k comparable · 283k input · 73k output · 12.4M cache reads, not counted · 12.7M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 3 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · summary\_lane only needs exception classes, like core/ingestion/llm.py which is allowlisted: allowlist it too? → Route it through aws\_account.endpoint\_unreachable. Why: Keeps summary\_lane under the guard at the cost of a four-line helper; llm.py is not a file this task may change, so it keeps its entry (DESIGN\_SPEC, agent)
- D2 · Keep the carrier's \_boto\_config and \_sign names? → Yes, as thin wrappers over aws\_account. Why: hive/agentcore\_provisioner calls ac.\_boto\_config; keeping the names keeps every caller and the signing test unchanged (DESIGN\_SPEC, agent)
- D3 · Where do the helpers import botocore? → Inside each helper, lazily. Why: aws\_account is imported on the AgentCore microVM and at daemon start; a top-level botocore import would add its load to every importer. A repeated import inside a function is a dict lookup (DESIGN\_SPEC, agent)
- D4 · Run the full routers/hive consumer list (16 test files)? → Ran the hive account / fleet / swarmai-account / verify files, which reach verify\_account and the provisioner; not the brain-sync, update and workspace files. Why: The routers/hive change is one Config in verify\_account's Bedrock check; the other files never reach it (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Add the five modules to ALLOWED, each with a reason (Config only, SigV4 only, exceptions only), as the test's own message offers and as was done for core/ingestion/llm.py. Not taken: An allowlisted module is unguarded as a whole: a later boto3.client() or boto3.Session() in the 1400-line carrier or the provisioner would pass the guard. None of the five needs the SDK for anything a helper cannot give it, and their sessions already come from the account layer.
- A3 · Narrow the test so that only boto3 / client-making imports count (ignore botocore.config, botocore.exceptions). Not taken: Changes the test's rule (the task forbids weakening it); botocore.session can make a default-chain client too.

Requirements analysis:

- missing\_edge (R1.2): sdk\_missing reads botocore's service model and \_check\_sdk prints botocore's version; neither makes a client, but both import botocore. They go through two small aws\_account helpers (service\_model, sdk\_version) so the provisioner stays under the guard. → rewritten
- missing\_edge (R1.3): Allowlisting the carrier for 'Config and SigV4 only' would also let any later direct boto3 client in that 1400-line module pass unseen; routing the helpers keeps the guard live there. The test's allowlist is therefore not extended. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 2/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ b0222a570cea: 8 pass, 0 fail, 0 not run
- test · swarmai @ 29758cdf9d2d: 5 pass, 0 fail, 2 not run
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
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- python -m pytest tests/test\_aws\_account\_integrity.py tests/test\_aws\_account\_sdk\_helpers.py tests/test\_carriers\_agentcore.py tests/test\_elastic\_hive\_provisioning.py tests/test\_hive\_agentcore\_mode.py te → pass (185 tests, exit 0; the integrity test fails on the base tree)
- python -m pytest tests/test\_aws\_account\_integrity.py tests/test\_aws\_account\_sdk\_helpers.py tests/test\_carriers\_agentcore.py -q --timeout=60 → pass (40 tests, exit 0, re-run after the docstring-only correction reviewed in the second visit)
- python scripts/smoke\_import.py → pass (All 695 discovered modules imported)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets, or wildcard CORS)
- bash scripts/run\_bvt.sh → pre\_existing (tests/test\_precommit\_uv\_lock.py fails 3/3 because no uv binary is on this machine's PATH (shutil.which('uv') is None), the same on the base tree, and --maxfail=3 then stops the run. The other 77 BVT f)
- python -m pytest tests/test\_verify\_bedrock\_stub.py -v --timeout=60 → pre\_existing (the 3 routers/system.py API-key verify tests fail the same way on the base tree ('Bearer authorization requires an HTTPS endpoint'); the routers/hive account-check test passes)
- python -m scripts.feature\_map replay --affected --run run\_5c67a1c9 → not\_run (environment: this Hive's installed SwarmAI reads features.yaml schema 1 and the repo's is schema 2 ('update SwarmAI'); the change has no UI surface)
- skills-lint, experts-lint, desktop-typecheck, desktop-contract-lint, desktop-build, version-check, frontend-run-frontend-tests → not\_run (the change touches no skill, expert, desktop or version file)

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: tests/test\_precommit\_uv\_lock.py fails 3/3 because no uv binary is on this machine's PATH (shutil.which('uv') is None), the same on the base tree, and --maxfail=3 then stops the run. The other 77 BVT files, run with the script's own flags and --maxfail=1000: 1331 tests, exit 0.
- swarmai · python -m pytest tests/test\_verify\_bedrock\_stub.py -v --timeout=60: the 3 routers/system.py API-key verify tests fail the same way on the base tree ('Bearer authorization requires an HTTPS endpoint'); the routers/hive account-check test passes

## Not run (the agent's reason, not checked)

- swarmai · python -m scripts.feature\_map replay --affected --run run\_5c67a1c9: environment: this Hive's installed SwarmAI reads features.yaml schema 1 and the repo's is schema 2 ('update SwarmAI'); the change has no UI surface
- swarmai · skills-lint, experts-lint, desktop-typecheck, desktop-contract-lint, desktop-build, version-check, frontend-run-frontend-tests: the change touches no skill, expert, desktop or version file

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_aws\_account\_sdk\_helpers.py
