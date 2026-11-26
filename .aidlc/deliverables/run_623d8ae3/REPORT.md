# Pollinate overlay 不显示正在做的作品：视频 skill 只在交付时 adopt（且 adopt 要求 out/\*.mp4），新 init 的… · Audit through DELIVER

Delivered: Pollinate overlay 不显示正在做的作品：视频 skill 只在交付时 adopt（且 adopt 要求 out/\*.mp4），新 init 的 deck 没有 current、未出片的视频都不出现在'在改的'区。要求：(1) work.py adopt --kind video 允许在 brief/storyboard 阶段就登记（无 mp4），视频 skill 开工时就登记；(2. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 24m 11s wall · 24m 11s active · 11.2% budget |
| Quality | WARN | 1/1 verification · 2/4 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 343k comparable · 265k input · 78k output · 19.7M cache reads, not counted · 20.0M billed · $7.62 book cost · host-session responses in the run window · 26.3% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 9 · design recalled · 7 · cited 5 · searched the DDD 1×

## Decisions

- D1 · Which video stages to show → brief, storyboard, scenes (frames k of n), film. Why: each is a file the product already reads; a voice stage would need the skill's audio folder name, which the product must not know (DESIGN\_SPEC, agent)
- D2 · What counts as in progress for a deck, poster, page or copy → an open revision, or no current version yet. Why: a work with no passed version has nothing finished to show; an open revision is being changed (DESIGN\_SPEC, agent)
- D3 · What a video's updated time is → the newest of its registry stamp and the mtimes of brief.md, storyboard.json and its mp4. Why: a video registered at the brief would otherwise keep the brief's time after the film is delivered (DESIGN\_SPEC, agent)
- D4 · How the card reaches the chat making a video → adopt --kind video records SWARM\_APP\_SESSION\_ID as work.json session, and the row and view carry it. Why: an in-progress video has no open record or versions to carry a session, and Open session is the card's useful action while it is being made (DESIGN\_SPEC, agent)
- D5 · The top zone's title now that it holds more than revisions → IN PROGRESS / 进行中 (was BEING REVISED / 在改). Why: a video being made or a deck not yet cut is not being revised (BUILD report · swarmai, agent)
- D6 · A card's age without an open revision → when the work last changed (updated), labelled changed. Why: there is no open time; the last change says whether the work is moving (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Derive in-progress from live chat sessions (a session streaming with Pollinate or video tools). Not taken: a session does not say which folder it is making; mapping sessions to folders needs the same registration, and a work open in a closed tab would vanish
- A3 · A new work.py stage command the video skill calls at each step to write its stage into work.json. Not taken: the stage would depend on the agent remembering to call it at each step (prose discipline, the failure mode of the old delivery-only registration); the files already show the stage mechanically

Requirements analysis:

- missing\_edge (R2.3): voice is not a stage: no file in the generic video contract (brief.md, storyboard.json, scene frames, out/\*.mp4) says the voice is done; the product must not name a skill's audio folder → rewritten
- missing\_edge (R2.4): a video revised after delivery keeps its mp4 and so reads as finished; the video skill revises it outside work.py, so there is no open record to read → confirmed
- missing\_edge (R1.1): registering early means the video skill's instructions (outside this repo) must move the adopt step to the brief; that edit and registering the current project are done outside the run → confirmed

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 7/26 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 29fa35d7f59c: 8 pass, 0 fail, 0 not run
- test · swarmai @ 7ba102ab3218: 6 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: not run
  - backend-security-scan: not run
  - skills-lint: pass
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_623d8ae3/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/Pollinate-Content-Engine.md
- changed paths the targets ignore: backend/tests/test\_pollinate\_in\_progress.py, desktop/src/components/layout/PollinateOverlay.inprogress.test.tsx, desktop/src/components/layout/PollinateOverlay.test.tsx


TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python -m pytest tests/test\_pollinate\_in\_progress.py tests/test\_pollinate\_works\_core.py tests/test\_pollinate\_router.py tests/test\_app\_registry.py skills/s\_pol → pass (all green after regenerating backend/generated/apps\_index.json (work.py changed in the R1 fix, which moves the skill's hash))
- npx vitest run src/components/layout/PollinateOverlay.inprogress.test.tsx src/components/layout/PollinateOverlay.test.tsx src/components/layout/\_\_tests\_\_/PollinateOverlay.loaderror.test.tsx src/servic → pass (4 files, 18 tests)
- npx tsc --noEmit -p tsconfig.json → pass (no type errors)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_623d8ae3 → pass (31 walks passed, 0 failed)
- python scripts/lint\_skills.py → pass (43 SKILL.md, 683 corpus files clean)
- python scripts/smoke\_import.py → pass (710 modules imported)

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/skills/s\_pollinate/tests/test\_work\_video\_early.py
- swarmai: backend/tests/test\_pollinate\_in\_progress.py
- swarmai: desktop/src/components/layout/PollinateOverlay.inprogress.test.tsx
