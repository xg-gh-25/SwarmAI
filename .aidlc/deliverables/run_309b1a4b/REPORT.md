# KNOWLEDGE.md 按 A 退役：KNOWLEDGE.md stops being one of SwarmAI's governed context … · Audit through DELIVER

Delivered: KNOWLEDGE.md 按 A 退役：KNOWLEDGE.md stops being one of SwarmAI's governed context files. It is no longer injected into the system prompt, no longer seeded into a new workspace's .context/, and no longer. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 45m 14s wall · 45m 14s active · 20.9% budget |
| Quality | WARN | 1/1 verification · 2 not run · 3/3 findings closed · a requirement not covered by the design · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 1.1M comparable · 901k input · 243k output · 82.3M cache reads, not counted · 83.4M billed · host-session responses in the run window · 87.6% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 0 (+2 found on demand) · searched the DDD 1×

## Decisions

- D1 · Keep .context/KNOWLEDGE.md in the workspace .gitignore privacy rules? → keep. Why: an existing copy stays on disk; it must stay out of git like before (DESIGN\_SPEC, agent)
- D2 · TIMELINE keeps priority 10 or moves to 9? → keep 10. Why: priorities only order the files; renumbering churns tests and texts for no behaviour change (DESIGN\_SPEC, agent)
- D3 · What happens to the loops-health checks that read KNOWLEDGE.md (K2 currency, K3 / X1 capability-to-knowledge sync)? → remove the KNOWLEDGE.md dependency: K2 retired, the sync checks read only EVOLUTION / MEMORY or are retired where they only compared against KNOWLEDGE.md. Why: they measure a file that is no longer part of cognition (DESIGN\_SPEC, agent)
- D4 · How does the privacy weld test account for the .gitignore line kept for old KNOWLEDGE.md copies? → Add RETIRED\_CONTEXT\_FILES (one entry, with reason) so the privacy weld test accounts for the kept .gitignore line instead of mis-calling KNOWLEDGE.md a channel carve-out. Why: D1 keeps the .gitignore rule; the weld test needs a named home for a private file that is no longer a context file (BUILD report · swarmai, agent)
- D5 · The template-drift collect test was already red on the base; fix it in this run? → Fix test\_collect\_includes\_the\_drift\_source to the counts shape that now carries handler totals. Why: it was red on the base (counts gained person/agent keys in the Needs You change); a one-line fix in a file this run already edits (BUILD report · swarmai, agent)
- D6 · What happens to the test checking the KNOWLEDGE template's Archives retention text? → Delete test\_knowledge\_md\_archives\_retention\_matches\_the\_hook. Why: its subject (the KNOWLEDGE template's Archives entry) no longer exists; the retention behaviour itself stays covered by test\_retention\_policies (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep the spec but stop injecting it behind a flag. Not taken: keeps a dead file in the owner map, seeding, hooks and charter: the half-retired state the request rules out (R3)
- A3 · Delete existing users' .context/KNOWLEDGE.md on startup. Not taken: destroys a file the user or Swarm wrote; an unread file costs nothing (R2.2, SOUL P4 recovery preserves)

Requirements analysis:

- missing\_edge (R2.2): template drift (context\_template\_drift) and the workspace seeding list both know KNOWLEDGE.md; a stale entry there could flag or recreate the user's file → rewritten
- missing\_edge (R1.2): the Context &amp; Memory overlay renders per\_file dynamically and is being rewritten in a parallel session; this run must not edit CMBrainOverlay.tsx or its test → rewritten
- missing\_edge (R4.1): the precedent is PROJECTS.md (retired 2026-08-14, commit 91d781e1): loader spec, SWARMAI.md, AGENT.md R24 clause and the repository AGENTS.md count were all updated; generated snapshots and agent-owned stores were left to their owners → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 9/26 tool calls
- correctness · review r1: read 100% of its prompt · 12/26 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 3a317a203a0b: 5 pass, 0 fail, 1 not run
- test · swarmai @ f56d63569386: 7 pass, 0 fail, 2 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: not run
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: not run
  - desktop-typecheck: not_run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_309b1a4b/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: AGENTS.md
- changed paths the targets ignore: backend/tests/test\_agent\_md\_r18\_r24\_text.py, backend/tests/test\_channel\_section\_filter.py, backend/tests/test\_context\_budget.py, backend/tests/test\_context\_directory\_loader.py, backend/tests/test\_context\_template\_drift.py, backend/tests/test\_context\_templates\_consistency.py, backend/tests/test\_inbox\_deletion\_sweep.py, backend/tests/test\_knowledge\_md\_retired.py, backend/tests/test\_loops\_health.py, backend/tests/test\_runtime\_hooks.py


TEST · swarmai:
- pytest tests/test\_knowledge\_md\_retired.py tests/test\_context\_directory\_loader.py tests/test\_context\_budget.py tests/test\_context\_brain\_token\_block.py tests/test\_context\_templates\_consistency.py tests/ → pass (286 passed)
- pytest tests/test\_system\_prompt\_e2e.py tests/test\_runtime\_hooks.py tests/test\_context\_health\_hook.py tests/test\_swarm\_workspace\_manager.py tests/test\_eval\_crud.py tests/test\_knowledge\_lifecycle\_reclai → pass (441 passed)
- python scripts/smoke\_import.py → pass (707 modules import)
- python scripts/lint\_skills.py → pass (44 SKILL.md, 723 corpus files clean)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- npx vitest run src/pages/chat/components/\_\_tests\_\_/TSCCOwnerMap.test.tsx src/pages/chat/components/\_\_tests\_\_/TSCCPanel.test.tsx src/services/\_\_tests\_\_/tscc.test.ts → pass (39 passed)
- py -m scripts.feature\_map replay --affected --run run\_309b1a4b → pass (31 walks pass, 0 fail)
- npx tsc --noEmit → not\_run (only frontend source change is one string literal in a typed array)
- bash scripts/run\_bvt.sh → not\_run (broad suite belongs to CI; the affected test files above were run directly)

Tests were not run again after main moved: tests carried over a main move: swarmai (main 33f7c2e995e1-&gt;2a21786398ed): main's new commits change none of the run's files, so the TEST report from before the sync stands

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: only frontend source change is one string literal in a typed array
- swarmai · bash scripts/run\_bvt.sh: broad suite belongs to CI; the affected test files above were run directly

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_knowledge\_md\_retired.py
