# Replace render.py's Python-Playwright browser layer with a Node driver script (… · Audit through DELIVER

Delivered: Replace render.py's Python-Playwright browser layer with a Node driver script (scripts/render\_driver.mjs) run on core.feature\_map.runtime's pinned Node and Playwright, driven over stdin JSON lines wit. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 50m 24s wall · 50m 24s active · 96.4% budget |
| Quality | WARN | 1/1 verification · 7 not run · 4/5 findings closed (7 reported; within 10 lines of one file they count once) (4 test-closed, confirmed by review) · CF-1e012add: no mutation check (the code under test is the test gate in conftest.py, itself a test-support file, so the engine refuses a mutation there as proof; run by hand, mutation\_check re) · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 692k comparable · 531k input · 162k output · 26.0M cache reads, not counted · 26.7M billed · $12.68 book cost · host-session responses in the run window · 66.8% budget |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+6 found on demand) · searched the DDD 3×

## Decisions

- D1 · How do frames cross the process boundary? → reply = JSON header line {id, ok, value\|error, events, bytes} then \`bytes\` raw bytes; a frame reply carries the decoded JPEG. Why: no base64 inflation on the pipe, one read per frame, and a JSON value of any size (tween lists) with no line-length limit problem because the stream reader limit is raised (DESIGN\_SPEC, agent)
- D2 · One request per frame or a streamed frame range? → one 'frame' request per frame doing seek then screenshot in the driver. Why: keeps render.py's per-frame error check and caption overrides in Python, and is one round trip per frame where the old path made two (evaluate, then CDP screenshot) (DESIGN\_SPEC, agent)
- D3 · Page events (pageerror, responses &gt;= 400, console warnings/errors, blocked requests)? → collected per page in the driver and delivered as deltas in every reply's \`events\`, appended to ScenePage's lists. Why: ScenePage keeps the same errors/missing/blocked/console lists and problems() text that check.py and the logs read (DESIGN\_SPEC, agent)
- D4 · Hang guard? → each request waits at most REQUEST\_HANG\_S (300 s); reaching it kills the driver and raises RenderError; the driver's exit fails every pending request with its stderr tail. Why: a genuine hang (a never-resolving seek, a dead Chromium) must not block a render forever; no real frame takes minutes (DESIGN\_SPEC, agent)
- D5 · Fingerprint and RENDER\_VERSION? → unchanged. Why: the output is meant to be the same frames; derive.py (another run) mirrors render.py's fingerprint (DDD e83329bd) and a version bump would force every film to re-render (DESIGN\_SPEC, agent)
- D6 · SKILL.md still declares playwright as an optional dep (file not owned here) → test\_skill\_contract compares imports with the declared set minus playwright and a new test forbids a playwright import; the SKILL.md change is handed to its owner in the final reply. Why: keeps the contract strict both before and after the SKILL.md update (DESIGN\_SPEC, agent)
- D7 · How do the existing browser tests decide to run? → a shared conftest.py gate that starts render.py's own browser once per session (skip without it, fail with MV\_REQUIRE\_BROWSER=1); test\_fonts\_render now drives the same driver (a cdp request) instead of Python Playwright. Why: the tests then exercise exactly the browser the product uses and run under the bundled interpreter (BUILD report · swarmai, agent)
- D8 · WebSockets → every WebSocket is closed by the driver; one not under the local server is listed as blocked. Why: Playwright's route does not see WebSockets; the old path relied only on the dead proxy for them (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Drive the pinned headless shell directly over CDP from Python (stdlib only). Not taken: the stdlib has no WebSocket client, and --remote-debugging-pipe needs inherited fds 3/4, which Python's subprocess cannot pass on Windows; it would also reimplement Playwright's context isolation, init scripts and request routing by hand
- A3 · A one-shot Node job that renders a whole scene (all frames) from a job file. Not taken: moves the ready wait, error checks, preview probe and tail captions into JavaScript and duplicates render.py's logic; the product decision for browser skills rejects it (DDD 6a0a5faa)
- A4 · Bundle Python Playwright into SwarmAI's build. Not taken: a second browser per install and its bundled Node driver fails on Hives (DDD 6a0a5faa, a96a1232); out of this task's files

Requirements analysis:

- missing\_edge (R1.4): test\_skill\_contract asserts that the set of third-party modules imported by scripts equals SKILL.md's declared deps; SKILL.md (not owned by this task) still declares playwright as optional, so removing the import breaks equality; handled: the contract test treats playwright as no longer imported (declared set minus playwright must equal imports, and a new assertion forbids importing it); the SKILL.md frontmatter and Requirements-table update is described in the final reply for its owner → rewritten
- missing\_edge (R2.1): Playwright page.route does not see WebSockets; the old path relied on the dead proxy for them; handled: the driver also closes every WebSocket and lists non-local ones as blocked (strictly tighter, same outcome) → rewritten
- missing\_edge (R3.3): the old path had no hang guard on a page evaluate or screenshot; handled: each driver request has a hang cap far above any real frame time; reaching it kills the driver and raises RenderError → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 4/26 tool calls
- correctness · review r1: read 100% of its prompt · 7/26 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 608d6d4ae99e: 4 pass, 0 fail, 0 not run
- test · swarmai @ b652246d7432: 8 pass, 0 fail, 7 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- – capabilities-view-6qa4 · \* [\*] not\_run: needs uv
- – chat-tab-draft-nfu9 · \* [\*] not\_run: needs uv
- – api-health-q56c · The backend answers health checks [command] not\_run: needs uv
- – chat-send-stream-h4kw · \* [\*] not\_run: needs uv
- – chat-tool-card-m2rp · \* [\*] not\_run: needs uv
- – chat-tabs-apart-x7tn · \* [\*] not\_run: needs uv
- – chat-stop-p9ds · \* [\*] not\_run: needs uv
- – chat-model-error-b3qz · \* [\*] not\_run: needs uv
- – chat-thinking-t5wq · \* [\*] not\_run: needs uv


TEST · swarmai:
- MV\_REQUIRE\_BROWSER=1 perl -e 'alarm 100; exec @ARGV or die' .venv/bin/python -m pytest skills/s\_motion-video/tests -q --timeout=60 -p no:cacheprovider → pass (107 tests, none skipped, browser tests on the pinned runtime under the backend interpreter)
- MV\_REQUIRE\_BROWSER=1 .venv/bin/python -m pytest skills/s\_motion-video/tests/test\_render\_runtime.py -q --timeout=60 -p no:cacheprovider → pass (includes test\_browser\_gate\_never\_installs\_the\_runtime)
- SWARM\_FEATURE\_MAP\_RUNTIME=/nonexistent perl -e 'alarm 100; exec @ARGV or die' .venv/bin/python -m pytest skills/s\_motion-video/tests -q --timeout=60 -p no:cacheprovider -rs → pass (without a runtime the 14 browser tests skip with the install hint instead of installing inside a 60 s test timeout; the rest pass)
- python scripts/smoke\_import.py → pass (712 modules import)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (first run flagged the dummy credential literal in test\_driver\_env\_is\_an\_allowlist; replaced by a DUMMY constant, re-run PASS)
- python scripts/lint\_skills.py → pass (44 SKILL.md, no machine-local references)
- python scripts/lint\_experts.py → pass (exit 0)
- npx tsc --noEmit → not\_run (no desktop file changed; run through task\_checks on the landed branch)
- npm run lint:contract → not\_run (no desktop file changed; run through task\_checks on the landed branch)
- 5 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run through task\_checks on the landed branch
- swarmai · npm run lint:contract: no desktop file changed; run through task\_checks on the landed branch
- swarmai · npm run build: no desktop file changed; run through task\_checks on the landed branch
- swarmai · bash scripts/sync-version.sh check: no version file changed; run through task\_checks on the landed branch
- swarmai · bash scripts/run\_bvt.sh: the BVT manifest lists backend/tests files only, none touched by this skill change; run through task\_checks on the landed branch
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run through task\_checks on the landed branch
- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_d7f918b4: environment: all 9 reached walks not\_run, 'uv is not on PATH' in the replay sandbox (the replay user cannot see the Hive user's \~/.local/bin); the reached walks are chat/capabilities UI walks a skill script change cannot affect

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/skills/s\_motion-video/scripts/render\_driver.mjs
- swarmai: backend/skills/s\_motion-video/tests/conftest.py
- swarmai: backend/skills/s\_motion-video/tests/test\_render\_runtime.py
