# 起 pipeline：job 去掉文件写工具。已定的系统化改法：无人值守的定时 agent job 一律不给 Write/Edit/MultiEdit/Not… · Audit through DELIVER

Delivered: 起 pipeline：job 去掉文件写工具。已定的系统化改法：无人值守的定时 agent job 一律不给 Write/Edit/MultiEdit/NotebookEdit（声明了也收回，并在结果摘要里说明；没声明工具的默认集也去掉它们），因为 headless 模式下这些工具没有按路径放行的机制（实测：dontAsk 会直接拿掉 Write；//\*\* 规则也会拿掉它），只能靠一份写不全的黑名. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 22m 29s wall · 22m 29s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 7 not run · 3/5 findings closed (9 reported; within 10 lines of one file they count once) (1 test-closed, confirmed by review) · tests not run again after main moved |
| Autonomy | PASS | 0 answers · 0 rescues · 1 required approval (approvals are oversight, not interventions) |
| Token Cost | N/A | 404k comparable · 305k input · 98k output · 21.3M cache reads, not counted · 21.7M billed · $8.66 book cost · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 0

## Findings

Requirements analysis:

- missing\_edge (R1.1): Measured on CLI 2.1.302: in dontAsk mode a path-scoped Edit(...) allow does not keep the Write tool (the CLI removes it), and a filesystem-wide Edit(//\*\*/...) deny also removes it; headless mode has no per-path allowlist for the file tools, which is why the deny list kept growing. → rewritten
- missing\_edge (R2.1, R2.3): The sandbox's allowWrite (workspace and temp dir) already keeps Bash out of the app data dir, \~/.claude and the home dir, so their deny rules served only the file tools; with those tools gone the rules protect nothing. → rewritten
- missing\_edge (R1.2): docs-freshness-audit and the GitHub community jobs declare Write; the GitHub jobs are moved to hand drafts back in their result (local job config, outside this repo); the audit job reports through its output already. → rewritten

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 13/25 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 3a51d59692fb: 7 pass, 0 fail, 0 not run
- test · swarmai @ 03a1b4b32427: 7 pass, 0 fail, 7 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_7a3cb6a9/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/design/2026-10-25-swarmai-app-platform-v1.md
- changed paths the targets ignore: backend/tests/test\_job\_sandbox.py, backend/tests/test\_protected\_paths.py


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_job\_sandbox.py tests/test\_protected\_paths.py tests/test\_job\_cli\_result.py tests/test\_usage\_accounting.py tests/test\_job\_system\_fixes.py tests/test\_restricted\_sess → pass (293 passed)
- bash scripts/run\_bvt.sh → pass (89 files, 1496 passed)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_7a3cb6a9 → pass (31 walks pass)
- python scripts/smoke\_import.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/lint\_skills.py → not\_run (no skill changed)
- python scripts/lint\_experts.py → not\_run (no expert changed)
- npx tsc --noEmit → not\_run (no frontend change)
- npm run lint:contract → not\_run (no frontend change)
- 4 more in run_audit.json

Tests were not run again after main moved: tests carried over a main move: swarmai (main 47563d246644-&gt;c9c165904d01): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Not run (the agent's reason, not checked)

- swarmai · python scripts/lint\_skills.py: no skill changed
- swarmai · python scripts/lint\_experts.py: no expert changed
- swarmai · npx tsc --noEmit: no frontend change
- swarmai · npm run lint:contract: no frontend change
- swarmai · npm run build: no frontend change
- swarmai · bash scripts/sync-version.sh check: no version change
- swarmai · npx vitest run --reporter=verbose: no frontend change
