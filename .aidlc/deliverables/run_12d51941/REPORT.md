# Replace work\_render.py's Python Playwright browser layer with a Node driver lau… · Audit through DELIVER

Delivered: Replace work\_render.py's Python Playwright browser layer with a Node driver launched from the pinned feature\_map runtime over a JSON-lines protocol, keeping every output and exit code. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 35m 20s wall · 35m 20s active · no baseline yet (0/5 runs) |
| Quality | PASS | 1/1 verification · 5/5 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 546k comparable · 427k input · 120k output · 17.0M cache reads, not counted · 17.5M billed · $9.20 book cost · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 1 (+1 found on demand) · searched the DDD 3×

## Decisions

- D1 · How does Python find core.feature\_map.runtime when the script is not run by the backend interpreter? → import core.feature\_map.runtime; when that fails, look for a backend folder (containing core/feature\_map/runtime.py) among this file's parents, add it to sys.path and retry; else RenderEnvError with one line telling to run it with "$SWARM\_BACKEND\_BIN" py. Why: the skill lives at &lt;backend&gt;/skills/s\_pollinate/scripts in both a checkout and an install, so a plain python in a checkout still works; outside a backend it exits 2 as the request says (DESIGN\_SPEC, agent)
- D2 · Which environment does the driver get? → the caller's environment without NODE\_OPTIONS and NODE\_PATH, plus Runtime.env() and POLLINATE\_PLAYWRIGHT (the file URL of the pinned @playwright/test index.mjs). Why: the same environment Python Playwright's driver inherited (fonts, display and library paths keep working on every OS); NODE\_OPTIONS/NODE\_PATH could load other code into the pinned Node (DESIGN\_SPEC, agent)
- D3 · What if the driver never answers? → a reader thread and a per-request wait of 300 s; past it the driver is killed and the render raises. Why: page.evaluate has no Playwright timeout; 300 s is far above the 60 s navigation and action timeout, so it only fires on a genuine hang (DESIGN\_SPEC, agent)
- D4 · Do the tests keep Python Playwright for inspecting output? → no: the JavaScript-off screenshot check in test\_work\_render.py and the probe page in test\_craft\_checks.py move to the driver client (work\_render.\_Browser). Why: Python Playwright is not in the backend or the repo venv, so those tests would only skip or fail; through the driver they run wherever the renderer runs (DESIGN\_SPEC, agent)
- D5 · Does core/feature\_map/runtime.py change? → no: Runtime.node, Runtime.playwright\_pkg/index.mjs and Runtime.env() are all the driver needs. Why: no public behaviour change (DESIGN\_SPEC, agent)
- D6 · render\_style\_thumbnails.py also imports Python Playwright → left as it is in this run and named in the final reply. Why: a maintainer script that makes gallery thumbnails once, not the work.py render/cut path the request names (DESIGN\_SPEC, agent)
- D7 · Which errors are environment errors (exit 2 from work.py, staged version removed by cut)? → runtime import failure, RuntimeUnavailable, a driver that cannot start or a Chromium that does not launch raise RenderEnvError; a failure of one request during a render raises RenderError (generic exit 2, as a Playwright error did). Why: keeps the existing exit-code contract (DESIGN\_SPEC, agent)
- D8 · render\_style\_thumbnails.py also imports Python Playwright (plan D6 left it out) → ported to the same driver in this run; its Google fonts now go through work\_render's font cache and inliner, so the browser still blocks the network. Why: kinds/deck.md has the agent run it when gallery thumbnails are missing, so it is on the product path and fails on every install the same way; dropping playwright from python-optional-deps is only true with it ported (BUILD report · swarmai, agent)
- D9 · test\_html\_deck\_track.py's two template tests use Python Playwright directly → left as they are (they skip without it). Why: they test the html-deck templates, not the renderer, and are outside the test set the task names (BUILD report · swarmai, agent)
- D10 · TEST found apps\_index.json stale after the review fixes and a test sentinel the security scan reads as a secret → regenerated the index and renamed the sentinel to AWS\_PROFILE in TEST. Why: both are required for the repo's own checks; the run goes back to REVIEW for that change (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · A one-shot job file: Python writes every step of the render into a job JSON, the driver runs it whole and writes results. Not taken: the orchestration and its findings would move into JavaScript and the contact sheet needs the slide PNGs first, so the logic would live twice or need a second launch; per-request lines keep the Python checks byte-for-byte as they are
- A3 · Bundle Python Playwright into the SwarmAI build. Not taken: the request asks for one renderer on the product's pinned runtime; Python Playwright carries its own Node driver, which fails on Hives (libatomic), and a second browser download per install
- A4 · Keep Python Playwright and point it at the runtime's node and browsers. Not taken: still needs the Python package the build does not carry

Requirements analysis:

- missing\_edge (R1.1): render\_style\_thumbnails.py also imports Python Playwright; it is a one-off maintainer script for gallery thumbnails, not the work.py product path named by the request; it stays out of scope for this run and is reported in the final reply → confirmed
- missing\_edge (R2.1): page.evaluate has no Playwright timeout, so a driver that never answers would hang the render; a per-request hang guard well above any Playwright timeout kills the driver and raises → rewritten
- missing\_edge (R4.1): pollinate skill tests are not in the CI BVT manifest; on a machine without the runtime need\_browser skips as before → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/26 tool calls
- security · review r2: read 100% of its prompt · 2/25 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 2e14e5c743b8: 6 pass, 0 fail, 0 not run
- test · swarmai @ fec648e97ed3: 9 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/0/0-0-test-finished-1.png
- ✓ first-run-setup-q8rx · setup wizard, then Configure later [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/1/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/2/0-0-test-finished-1.png
- ✓ pipeline-view-vmna · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/3/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/4/0-0-test-finished-1.png
- ✓ todo-view-55s5 · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/5/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/6/0-0-test-finished-1.png
- ✓ jobs-view-k98s · opens from the left nav and closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/7/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/8/0-0-test-finished-1.png
- ✓ settings-view-ze9a · opens with its sections, Escape closes [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/9/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/10/0-0-test-finished-1.png
- ✓ brain-hub-bvcc · shows the built-in Brains [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/11/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/12/0-0-test-finished-1.png
- ✓ capabilities-view-6qa4 · lists the apps, and the other views can be chosen [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/13/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/14/0-0-test-finished-1.png
- ✓ chat-tab-draft-nfu9 · a new tab starts empty and the first keeps its draft [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/15/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/16/0-0-test-finished-1.png
- ✓ no-account-banner-6atk · the banner opens Settings [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/17/0-0-test-finished-1.png
- ✓ api-health-q56c · The backend answers health checks [command] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/18/0-0-app-walk-api-health-q56c.log
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/19/0-0-test-finished-1.png
- ✓ chat-send-stream-h4kw · send, stream, survive a reload [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/20/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/21/0-0-test-finished-1.png
- ✓ chat-tool-card-m2rp · a Bash step, then the answer that quotes it [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/22/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/23/0-0-test-finished-1.png
- ✓ chat-tabs-apart-x7tn · two tabs stream at once, each keeps its own reply [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/24/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/25/0-0-test-finished-1.png
- ✓ chat-stop-p9ds · Stop ends the reply, adds nothing after, and the chat goes on [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/26/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/27/0-0-test-finished-1.png
- ✓ chat-model-error-b3qz · one error, no spinner, the next message works [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/28/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [compact] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/29/0-0-test-finished-1.png
- ✓ chat-thinking-t5wq · a thought, its answer, and the turn after [regular] pass
  - /home/swarm/.swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs/run\_12d51941/artifacts/feature-checks/swarmai/30/0-0-test-finished-1.png


TEST · swarmai:
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest skills/s\_pollinate/tests/test\_work.py skills/s\_pollinate/tests/test\_work\_common.py skills/s\_pollinate/tests/test\_work\_guard.py skills/s\_polli → pass (65 passed, 0 skipped)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest skills/s\_pollinate/tests/test\_work\_integration.py skills/s\_pollinate/tests/test\_craft\_checks.py skills/s\_pollinate/tests/test\_export\_pptx.py  → pass (78 passed, 0 skipped)
- perl -e 'alarm 115; exec @ARGV' .venv/bin/python -m pytest skills/s\_pollinate/tests/test\_work\_runtime.py tests/test\_app\_registry.py -q --timeout=100 -p no:cacheprovider --maxfail=1000 → pass (26 passed on the final tree (after the sentinel rename and the index regeneration))
- python scripts/smoke\_import.py → pass (711 modules import)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (PASS (a test sentinel named like a secret was renamed first))
- python scripts/lint\_skills.py → pass (44 SKILL.md pass)
- python scripts/lint\_experts.py → pass (13 expert files pass)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_12d51941 → pass (31 pass, 0 fail, 0 not\_run)

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/skills/s\_pollinate/scripts/work\_render\_driver.mjs
- swarmai: backend/skills/s\_pollinate/tests/test\_work\_runtime.py
