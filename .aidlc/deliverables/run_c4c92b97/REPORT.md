# XG 2026-11-26：「按 Pipeline overlay 的标准 重新设计下我们的 Pollinate overlay」「缺 motion-vide… · Audit through DELIVER

Delivered: XG 2026-11-26：「按 Pipeline overlay 的标准 重新设计下我们的 Pollinate overlay」「缺 motion-video 是吧」「历史上的pollinate 产物全都不留 我们不做向后兼容」「按 mockup 起 pipeline 实现 overlay」。Spec = Knowledge/Mockups/2026-11-26-pollinate-overla. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 1h 2m wall · 1h 2m active · 28.9% budget |
| Quality | WARN | 1/1 verification · 4 not run · 1/5 findings closed (1 test-closed, confirmed by review) · review coverage: correctness r1 read 43% of its prompt |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 902k comparable · 722k input · 180k output · 68.8M cache reads, not counted · 69.7M billed · $23.14 book cost · host-session responses in the run window · 69.1% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 5 (+3 found on demand) · searched the DDD 2×

## Decisions

- D1 · How is a revision identified for the first-pass rate → agent versions sharing the same base (the open record's base, written into each cut's entry) are one revision; the first in list order is its first cut. Why: work.py writes base on every cut, so no new field is needed and list order is the writer's order (DESIGN\_SPEC, agent)
- D2 · How does a video become a work without a source to snapshot → adopt --kind video registers the folder and writes work.json with kind video, title, size and no versions; open, cut and render refuse a video with exit 2. Why: the video skill renders and checks its own film; Pollinate only lists it (DESIGN\_SPEC, agent)
- D3 · What the big card shows as the brief → brief.md text as is, the band showing its first lines with the rest behind the card. Why: cards/brief.md defines the brief as free text; parsing it would guess (DESIGN\_SPEC, agent)
- D4 · What 'versions by the person' counts → versions with by=user and found\_on\_disk=true (absorbed hand edits); the adopted v001 is not counted. Why: the adopted version is the starting file, not an edit (DESIGN\_SPEC, agent)
- D5 · Brain of a work → the project name when the work path is under &lt;workspace&gt;/Projects/&lt;P&gt;/, otherwise General. Why: INSTRUCTIONS place new works under Projects/&lt;P&gt;/assets or Knowledge/Pollinate (DESIGN\_SPEC, agent)
- D6 · The mockup's big card had an Open folder command → dropped; the actions are Open in Canvas, Open session or Continue in chat. Why: the workspace explorer cannot open at a path today, so the button would land somewhere else; R4.3's open\_folder is not built (BUILD report · swarmai, agent)
- D7 · Which colour marks a version the person made → a new --line-person token with the same violet as --line-judge. Why: one meaning per colour name; the mockup used violet for the person (BUILD report · swarmai, agent)
- D8 · How the tests isolate the session id → the skill test fixture unsets SWARM\_APP\_SESSION\_ID. Why: the agent's own session would otherwise be written into every test work (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Frontend reads work.json and the version files itself through the workspace file API. Not taken: works outside the workspace cannot be read that way, the eval would need one request per version file, and the actions would be decided in the UI instead of one backend authority
- A3 · work.py appends cut events to a database table that the overlay queries. Not taken: a second store of the same facts that can drift from work.json; the files are already the authority and the counts are small

Requirements analysis:

- missing\_edge (R2.3): a video folder may have no storyboard.json or no image in out/ → rewritten
- missing\_edge (R4.1): a work outside the workspace cannot be opened in Canvas → rewritten
- missing\_edge (R5.1): a revision is identified by the base of its agent versions: versions cut from the same open share open.base → rewritten
- vague (R4.1): the brief is free text (cards/brief.md), not fields → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 9/29 tool calls
- correctness · review r1: read 43% of its prompt · 26/29 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ bc4379ff08a1: 10 pass, 0 fail, 0 not run
- test · swarmai @ d8e1a7b80fdb: 10 pass, 0 fail, 4 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: not_run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not_run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✗ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] fail (red at the run's base too): Error: the tab limit leaves room for a second tab
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/24/0-0-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/24/1-1-trace.zip
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/24/2-2-test-failed-1.png
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/24/3-3-trace.zip
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_c4c92b97/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png
- changed paths under no target: docs/Pollinate-Content-Engine.md
- changed paths the targets ignore: backend/tests/test\_pollinate\_router.py, backend/tests/test\_pollinate\_works\_core.py, desktop/src/components/layout/PollinateOverlay.test.tsx, desktop/src/components/layout/\_\_tests\_\_/PollinateOverlay.loaderror.test.tsx, desktop/src/components/layout/navOverlayContract.test.tsx, desktop/src/components/layout/overlayHostE2E.test.tsx, desktop/src/components/layout/overlayTiming.test.tsx, desktop/src/services/\_\_tests\_\_/pollinate.test.ts


TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest tests/test\_pollinate\_works\_core.py tests/test\_pollinate\_router.py tests/test\_router\_async\_blocking.py skills/s\_pollinate/tests -q --timeout=6 → pass
- npx vitest run src/components/layout/PollinateOverlay.test.tsx src/components/layout/\_\_tests\_\_/PollinateOverlay.loaderror.test.tsx src/components/layout/overlayTiming.test.tsx src/components/layout/ov → pass
- backend/.venv/bin/python scripts/smoke\_import.py → pass
- backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- backend/.venv/bin/python scripts/security\_scan.py → pass
- backend/.venv/bin/python scripts/lint\_skills.py → pass
- npx tsc --noEmit -p . → pass
- npm run -s lint:contract → pass
- npm run -s build → pass
- backend/.venv/bin/python backend/scripts/generate\_app\_registry.py --check → pass
- 4 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: the whole backend BVT; the change's own tests and the async-blocking scan ran above, and CI runs the BVT
- swarmai · npx vitest run --reporter=verbose: the whole frontend suite; every test file the change touches or that mocks the Pollinate service ran above
- swarmai · python scripts/lint\_experts.py: no expert prompt or catalog changed
- swarmai · bash scripts/sync-version.sh check: no version file changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/pollinate\_works.py
- swarmai: backend/skills/s\_pollinate/tests/test\_work\_session\_video.py
- swarmai: backend/tests/test\_pollinate\_works\_core.py
- swarmai: desktop/src/components/layout/PollinateWorkCard.tsx
- swarmai: desktop/src/services/\_\_tests\_\_/pollinate.test.ts
