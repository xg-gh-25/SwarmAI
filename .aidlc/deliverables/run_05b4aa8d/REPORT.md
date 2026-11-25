# ## Goal: a check that already fails on the base does not block landing a change… · Audit through DELIVER

Delivered: ## Goal: a check that already fails on the base does not block landing a change that did not cause it When a dispatched run's declared check fails on the Hive (scripts/task\_checks.py \`run\`, recorded w. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 39m 36s wall · 39m 36s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 2/6 findings closed (8 reported; within 10 lines of one file they count once) (1 test-closed, confirmed by review) · 4 fail-to-pass · 1 HIGH finding open |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 545k comparable · 420k input · 125k output · 21.6M cache reads, not counted · 22.1M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 5 · design recalled · 7 · cited 1 (+4 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which base environment → run the order's own setup lines on the base clone. Why: same setup as the change, from the base's own lockfiles; a setup line that fails on the base records base\_error and keeps errored (fail-closed) (DESIGN\_SPEC, agent)
- D2 · How the base clone is made → hive\_workspace.base\_clone reuses \_clone\_into with the task clone as the source. Why: same identity config, origin URL and remote-tracking refs as the change clone, no network, no mirror lock (DESIGN\_SPEC, agent)
- D3 · Which carriers' Host readers carry base\_exit → one cleaner swarm\_out\_results.check\_line used by both \_take\_check\_records (EC2) and agentcore.\_check\_lines, adding base\_exit only when it is an int. Why: one reader shape for both doors; a line without base\_exit is cleaned to exactly the old dict, so kept records compare equal (DESIGN\_SPEC, agent)
- D4 · The TEST-report fallback's pre\_existing → stays errored. Why: it is the agent's own word, not a measured base run (DESIGN\_SPEC, agent)
- D5 · Which repositories get a base clone → every repository of the order, at its order sha. Why: setup lines may name any of them; an order repository with no sha makes the base unavailable (DESIGN\_SPEC, agent)
- D6 · How the base clone avoids the task branch it inherits from the task clone → \_clone\_into gains detached=True for base\_clone. Why: cloning the task clone carries its hive/&lt;id&gt; branch, so checkout -b collides; detached keeps the identity config and refs (BUILD report · swarmai, agent)
- D7 · test\_dispatch\_sends\_checks\_by\_id\_only\_to\_a\_capable\_hive failed on the base (its fake \_require\_work\_orders took no needs argument) → fixed the fake in tests/test\_task\_spec.py. Why: the task requires tests/test\_task\_checks.py green; the fix is one test-only signature (BUILD report · swarmai, agent)
- D8 · The extraction of the shared line cleaner (R4 extract vs extend) → lands in the run's one commit. Why: the pipeline makes one landing commit; the extraction alone was verified green before the feature was added (BUILD report · swarmai, agent)
- D9 · What the agent is told → the task text and the s\_swarm-out skill say a failing check is re-run on the base (cap about twice its time) and that pre\_existing does not refuse landing. Why: an agent cap that ends the base run leaves the check errored, which is safe but useless (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Run the check on a base worktree with the change's recorded setup environment (env.json), without re-running setup. Not taken: setup output lives in the change clone (backend/.venv, desktop/node\_modules, .env); a base without it fails for environment reasons, which would read pre\_existing and let a change that broke the check land
- A3 · Keep a base clone set up once per work order across task\_checks calls. Not taken: the task asks for the base checkout to be removed afterwards; a kept tree carries earlier base runs' output into later ones; the saving is setup time on failures only
- A4 · Write only one end line after the base run. Not taken: a cap that ends task\_checks during the base run would leave only a start line, which reads unknown and does not refuse landing; the check's own end line must be written first

Requirements analysis:

- missing\_edge (R1.1): the base clone has no setup output (no .venv, no node\_modules), so running the check there with the change's environment would fail for environment reasons and wrongly read pre\_existing, letting a broken change land → rewritten
- missing\_edge (R1.1): the agent caps each task\_checks call; a cap that ends the process during the base run must leave the check reading errored → rewritten
- missing\_edge (R2.1): a change that adds a new failure inside a check that already failed on the base still reads pre\_existing: the exit code cannot tell them apart; the card shows it distinctly so the person sees it → confirmed
- missing\_edge (R2.3): the run's TEST-report fallback (no record) maps an agent-written pre\_existing to errored; it stays errored, since it is the agent's word, not a measured base run → confirmed
- missing\_edge (R2.1, R2.6): every reader normalizes a status through a fixed set (check\_status in swarm\_out\_results, checkStatus in live.ts, the SwarmOutCheckStatus type, runReview's missed list): each must learn pre\_existing or it reads as not\_seen → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/26 tool calls
- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 647a87fbb5a6: 8 pass, 0 fail, 0 not run
- test · swarmai @ f47105ecc270: 13 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: pass
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- – first-run-setup-q8rx · \* [\*] not\_run: needs uv
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- – pipeline-view-vmna · \* [\*] not\_run: needs uv
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- – todo-view-55s5 · \* [\*] not\_run: needs uv
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- – jobs-view-k98s · \* [\*] not\_run: needs uv
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- – settings-view-ze9a · \* [\*] not\_run: needs uv
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- – brain-hub-bvcc · \* [\*] not\_run: needs uv
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- – capabilities-view-6qa4 · \* [\*] not\_run: needs uv
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- – chat-tab-draft-nfu9 · \* [\*] not\_run: needs uv
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- – no-account-banner-6atk · \* [\*] not\_run: needs uv
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/27/0-0-app-walk-api-health-q56c.log
- – chat-send-stream-h4kw · \* [\*] not\_run: needs uv
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- – chat-tool-card-m2rp · \* [\*] not\_run: needs uv
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/32/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/33/0-0-test-finished-1.png
- – chat-tabs-apart-x7tn · \* [\*] not\_run: needs uv
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/35/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/36/0-0-test-finished-1.png
- – chat-stop-p9ds · \* [\*] not\_run: needs uv
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/38/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/39/0-0-test-finished-1.png
- – chat-model-error-b3qz · \* [\*] not\_run: needs uv
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/41/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/42/0-0-test-finished-1.png
- – chat-thinking-t5wq · \* [\*] not\_run: needs uv
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/44/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_05b4aa8d/artifacts/feature-checks/swarmai/45/0-0-test-finished-1.png
- changed paths the targets ignore: backend/tests/test\_check\_record\_reaches\_host.py, backend/tests/test\_swarm\_out\_actor.py, backend/tests/test\_swarm\_out\_results.py, backend/tests/test\_task\_checks.py, backend/tests/test\_task\_spec.py, desktop/src/components/swarm-out/brain.test.tsx


TEST · swarmai:
- python -m pytest tests/test\_task\_checks.py tests/test\_swarm\_out\_results.py tests/test\_swarm\_out\_actor.py tests/test\_check\_record\_reaches\_host.py tests/test\_task\_spec.py tests/test\_hive\_workspace.py te → pass (282 tests: the change's tests and the touched modules' suites)
- npx vitest run src/components/swarm-out/brain.test.tsx src/i18n/\_\_tests\_\_/locale-keys.test.ts src/components/swarm-out/\_\_tests\_\_/SwarmOutSingleEntry.test.tsx → pass (36 tests)
- bash scripts/run\_bvt.sh → pass (1380 tests, with the task's tool PATH (uv))
- python scripts/security\_scan.py → pass (no new HIGH findings)
- python scripts/smoke\_import.py → pass (all modules import)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/lint\_skills.py → pass (the s\_swarm-out text)
- python scripts/lint\_experts.py → pass (ok)
- npx tsc --noEmit → pass (ok)
- npm run lint:contract → pass (ok)
- 4 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx vitest run --reporter=verbose: the whole frontend suite takes longer than the 590 s a call may take on this Hive; the change's own frontend tests and the locale/run-card tests ran above, and it runs through task\_checks on the landed branch
