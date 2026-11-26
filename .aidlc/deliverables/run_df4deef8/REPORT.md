# # Fix C: on a fresh install, the second start permanently deletes user files (d… · Audit through DELIVER

Delivered: # Fix C: on a fresh install, the second start permanently deletes user files (data loss) \`backend/core/swarm\_workspace\_manager.py\` has a startup step, \`\_cleanup\_legacy\_content\`, that deletes legacy na. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 23m 9s wall · 23m 9s active · no baseline yet (3/5 runs) |
| Quality | WARN | 1/1 verification · 1 red at base · 1/4 findings closed (5 reported; within 10 lines of one file they count once) (1 test-closed, confirmed by review) · 1 fail-to-pass · 1 HIGH finding open |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 482k comparable · 394k input · 88k output · 12.2M cache reads, not counted · 12.7M billed · $7.34 book cost · host-session responses in the run window · no baseline yet (3/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · What tells a start that the workspace is fresh? → The workspace root does not exist when ensure\_default\_workspace begins. Why: It is an observation of real data, not a flag (DDD 11a82496: freshness authority = real data); a root this call creates cannot contain legacy content (DESIGN\_SPEC, agent)
- D2 · Where do moved items go and how is the folder named? → &lt;root&gt;/.trash/legacy-&lt;YYYYmmdd-HHMMSS&gt;-&lt;6 hex&gt;/&lt;same relative path&gt;. Why: Same .trash + timestamp-suffix shape delete\_project uses for Projects/.trash; the random suffix keeps two cleanups in one second apart (DESIGN\_SPEC, agent)
- D3 · Move with rename or shutil.move? → Path.rename, refusing an existing destination. Why: The trash lives inside the workspace (same filesystem), so rename is atomic; shutil.move's copy+delete fallback could leave a half-copied item; on EXDEV the item stays in place and is logged (DESIGN\_SPEC, agent)
- D4 · Creation branch over a directory that already existed? → No marker; the next start runs the now non-destructive cleanup, as before. Why: Such a directory may hold real legacy content; trash-moving it is safe, marking it clean would skip it forever (DESIGN\_SPEC, agent)
- D5 · Write the marker when some legacy moves failed? → Yes, as before. Why: A failed item stays in place (nothing lost) and is logged; not writing it would make every later start move user files created under legacy names into the trash (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Write the marker unconditionally in the creation branch and swap verify\_integrity before the cleanup in the existing branch so the marker write succeeds on the seed path. Not taken: Uses the DB row, not the directory, as the freshness signal: a creation-branch start over a pre-existing directory with real legacy content would be marked clean without cleaning, and the reorder changes the order of an existing branch for every returning user.
- A3 · Narrow the legacy name lists or match legacy file content before removing. Not taken: index.md, chats/ and workspace/ are ordinary names a user picks; no content test separates them reliably, and it would still destroy whatever it matched.
- A4 · Keep rmtree/unlink but copy each item to a backup first. Not taken: A rename into a trash folder is the backup and the removal in one atomic step; copy-then-delete doubles the I/O and has a partial state between the two.

Requirements analysis:

- missing\_edge (R1.2): Seed-DB first start: the config row exists but the directory does not; the cleanup must run on the directory only after verify\_integrity created it, so the marker can be written, or the absence of the directory is itself the freshness signal. → rewritten
- missing\_edge (R2.1): Two cleanups in the same second, or a relative path already present in the trash folder: the folder name carries a timestamp plus a short random suffix so a run never collides with an earlier one. → rewritten
- missing\_edge (R1.1): Creation branch where the root directory already existed (config row missing but an old directory present): it may hold legacy content, so the marker is written only when the root did not exist before this start; otherwise the next start runs the (now non-destructive) cleanup. → rewritten
- missing\_edge (R2.3): Sibling of the same shape: database/sqlite.py's swarm\_workspaces migration rmtrees legacy multi-workspace directories (excluding the live SwarmWS and anything outside the app data dir). It is a different mechanism triggered only by the old swarm\_workspaces table; this run scopes it out and reports it as a follow-up. → confirmed

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 6/25 tool calls
- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 15/25 tool calls
- correctness · review r2: read 100% of its prompt · 17/25 tool calls
- security · review r2: read 100% of its prompt · 10/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 9d4386683bd9: 4 pass, 0 fail, 0 not run
- test · swarmai @ 985ce073b0f5: 5 pass, 0 fail, 0 not run
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
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_df4deef8/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths the targets ignore: backend/tests/cdf\_materializer\_allowlist.txt, backend/tests/test\_legacy\_content\_trash.py, backend/tests/test\_swarm\_workspace\_manager.py


TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV or die' .venv/bin/python -m pytest tests/test\_legacy\_content\_trash.py tests/test\_initialization.py tests/test\_legacy\_cleanup.py tests/test\_cdf\_materializer\_static.py -q  → pass (61 passed: the 8 new tests (fail-to-pass on base) plus ensure\_default\_workspace's callers and the writer inventory)
- perl -e 'alarm 115; exec @ARGV or die' .venv/bin/python -m pytest tests/test\_swarm\_workspace\_manager.py -q --timeout=60 --maxfail=1000 → pre\_existing (only TestEnsureGitRepo::test\_creates\_initial\_commit and ::test\_commits\_existing\_files fail, identically on the base tree (no git identity on this Hive, so the initial commit is not made); all other te)
- backend/.venv/bin/python scripts/smoke\_import.py → pass (710 modules import)
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (clean)
- backend/.venv/bin/python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- $SWARM\_BACKEND\_BIN py -m scripts.feature\_map replay --affected --run run\_df4deef8 → pass (31 of 31 affected walks pass)

## Red at the run's base too (the agent's report, not checked)

- swarmai · perl -e 'alarm 115; exec @ARGV or die' .venv/bin/python -m pytest tests/test\_swarm\_workspace\_manager.py -q --timeout=60 --maxfail=1000: only TestEnsureGitRepo::test\_creates\_initial\_commit and ::test\_commits\_existing\_files fail, identically on the base tree (no git identity on this Hive, so the initial commit is not made); all other tests pass

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_legacy\_content\_trash.py
