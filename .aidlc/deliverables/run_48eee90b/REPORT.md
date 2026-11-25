# Pollinate v2 part P6: move /assets off the event loop with a helper-aware scann… · Audit through DELIVER

Delivered: Pollinate v2 part P6: move /assets off the event loop with a helper-aware scanner, add GET /works from the registry, show works with Open in Canvas in the overlay, and delete the unused detail and pub. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 58m 36s wall · 58m 36s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 2 not run · 3/3 findings closed (4 reported; within 10 lines of one file they count once) (2 test-closed, confirmed by review) · CF-0c556aa1: no mutation check (the fix is in the helper scanner, which lives in a test file (backend/tests/test\_router\_async\_blocking.py), so the engine's mutation proof cannot take it out of) · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 830k comparable · 682k input · 148k output · 32.1M cache reads, not counted · 32.9M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 2 (+4 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which main output does /works point at per kind? → deck: out/deck.html then out/deck.pdf; poster: out/poster.png; page: out/page.html then out/page.png; copy: src/copy.md; null when none exists. Why: §13.2 names the fixed outputs; Canvas renders HTML, and copy has no out/ file (DESIGN\_SPEC, agent)
- D2 · How are works outside the workspace returned? → in\_workspace=false, paths absolute, no Open in Canvas and no thumbnail in the overlay. Why: §4: Canvas and /workspace/file/raw open only workspace files; an outside path in open-canvas-file is unverified (DDD 6ee0d9c1) (DESIGN\_SPEC, agent)
- D3 · What happens to asset\_id, posted\_url and the publish-state.json reader once POST /publish is deleted? → removed from the router, the schema and the TS types; the kit frontmatter status stays. Why: They exist only for the deleted write path; with no writer the sidecar reader is dead code (P9) (DESIGN\_SPEC, agent)
- D4 · Where do helper-level blocking calls in other routers go? → into the same async\_blocking\_baseline.json ratchet, keyed by file, counting helper-level findings for routers/channels and direct findings for core/. Why: One ratchet pattern in the repo; a new helper-level call anywhere in routers/channels turns the gate red (DESIGN\_SPEC, agent)
- D5 · Where does the stats strip go now that works lead the gallery? → the run stats strip moves to the Insights view only; the gallery view leads with works. Why: design-judgment: one decision per surface; run counts are about old runs, not about the works the person reviews (DESIGN\_SPEC, agent)
- D6 · The task listed only PollinateOverlay\*, services/pollinate.ts and the router; what else had to change? → also schemas/pollinate.py (the router's models), overlayQueries.ts (the one place an overlay's queries live), en/zh locale files (new text, dead publish keys removed: the ui-keys-used test requires it), and the mocks of overlayHostE2E/overlayTiming tests. Why: none of these files is owned by another part; leaving them would break the build or the i18n tests (BUILD report · swarmai, agent)
- D7 · Did the dispatch set of the scanner need run\_nav? → added run\_nav and run\_nav\_action to the exempt dispatchers. Why: executors.run\_nav(lambda: ...) is off-loop like run\_in; without it the lambda form would be a false positive (BUILD report · swarmai, agent)
- D8 · How to make the helper scan safe on recursive graphs? → a fixed point over the call graph instead of a memoized depth-first walk. Why: no cut flags or cycle handling to get wrong; at most one pass per helper (REVIEW report · swarmai, agent)
- D8 · How to make the helper scan safe on recursive graphs? → a fixed point over the call graph instead of a memoized depth-first walk. Why: no cut flags or cycle handling to get wrong; at most one pass per helper (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Scanner: add a runtime test that patches Path.iterdir/read\_text to raise when called on the event-loop thread, and call the endpoint through TestClient. Not taken: It only proves the endpoints it calls; it does not guard any future router, and TestClient runs the app in a portal thread whose loop identity makes the check fragile. The AST scanner is the repo's existing class-wide guard; making it helper-aware closes the hole for every router.
- A3 · Scanner: make helper-following zero tolerance across all of routers/ and channels/, and fix the 13 other helper-level calls (mcp.py, workspace.py, workspace\_api.py, library\_api.py) in this run. Not taken: Those files are outside this task's part and include the workspace read/write hot paths; widening this diff reopens review of unrelated code. The ratchet stops the class growing now; the 13 sites are reported for a separate fix.
- A4 · Frontend: replace the old run gallery with works only. Not taken: The task keeps the existing asset gallery for old runs below the works section.

Requirements analysis:

- missing\_edge (R2.1): a work registered outside the workspace (design §4) cannot be opened in Canvas or thumbnailed through the workspace-sandboxed file endpoint. Handling: the endpoint returns in\_workspace=false with absolute paths; the card shows no thumbnail and no Open in Canvas action → confirmed
- missing\_edge (R2.1): §13 does not name a main output for page and copy. Handling: deck: out/deck.html then out/deck.pdf; poster: out/poster.png; page: out/page.html then out/page.png; copy: src/copy.md; null when none exists → confirmed
- missing\_edge (R1.4): the helper-aware scan finds existing helper-level blocking calls in mcp.py, workspace.py, workspace\_api.py and library\_api.py that this task does not own. Handling: frozen per file in the existing async\_blocking\_baseline.json ratchet; reported for a separate fix → confirmed
- missing\_edge (R4.1): with no writer left, the publish-state.json sidecar reader in \_walk\_assets is dead code. Handling: removed together with the write path; kit frontmatter status stays → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 5/26 tool calls
- correctness · review r1: read 100% of its prompt · 15/27 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls
- security · review r1: read 100% of its prompt · 1/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- correctness · review r2: read 100% of its prompt · 12/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 63e4cf6eb550: 8 pass, 0 fail, 0 not run
- test · swarmai @ 3eb648a9a6a1: 8 pass, 0 fail, 2 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- – first-run-setup-q8rx · \* [\*] not\_run: runtime unavailable
- – pipeline-view-vmna · \* [\*] not\_run: runtime unavailable
- – todo-view-55s5 · \* [\*] not\_run: runtime unavailable
- – jobs-view-k98s · \* [\*] not\_run: runtime unavailable
- – settings-view-ze9a · \* [\*] not\_run: runtime unavailable
- – brain-hub-bvcc · \* [\*] not\_run: runtime unavailable
- – capabilities-view-6qa4 · \* [\*] not\_run: runtime unavailable
- – chat-tab-draft-nfu9 · \* [\*] not\_run: runtime unavailable
- – no-account-banner-6atk · \* [\*] not\_run: runtime unavailable
- – api-health-q56c · The backend answers health checks [command] not\_run: runtime unavailable
- – chat-send-stream-h4kw · \* [\*] not\_run: runtime unavailable
- – chat-tool-card-m2rp · \* [\*] not\_run: runtime unavailable
- – chat-tabs-apart-x7tn · \* [\*] not\_run: runtime unavailable
- – chat-stop-p9ds · \* [\*] not\_run: runtime unavailable
- – chat-model-error-b3qz · \* [\*] not\_run: runtime unavailable
- – chat-thinking-t5wq · \* [\*] not\_run: runtime unavailable
- changed paths the targets ignore: backend/tests/async\_blocking\_baseline.json, backend/tests/test\_pollinate\_router.py, backend/tests/test\_router\_async\_blocking.py, desktop/src/components/layout/PollinateOverlay.test.tsx, desktop/src/components/layout/\_\_tests\_\_/PollinateOverlay.loaderror.test.tsx, desktop/src/components/layout/overlayHostE2E.test.tsx, desktop/src/components/layout/overlayTiming.test.tsx


TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python -m pytest tests/test\_pollinate\_router.py tests/test\_router\_async\_blocking.py -q --timeout=60 --maxfail=1000 -p no:cacheprovider → pass (60 passed on the final tree (fixed-point helper scan; newest-version status))
- python scripts/smoke\_import.py → pass (all 711 modules import)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (PASS, no new HIGH findings)
- npx tsc --noEmit → pass (exit 0)
- npm run lint:contract → pass (exit 0)
- npm run build → pass (built; bundle size under caps)
- perl -e 'alarm 280; exec @ARGV' npx vitest run --reporter=verbose src/components/layout/PollinateOverlay.test.tsx src/components/layout/\_\_tests\_\_/PollinateOverlay.loaderror.test.tsx src/components/lay → pass (45 passed: the vitest files this change touches (the full suite runs once through task\_checks after landing))
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_48eee90b → not\_run (environment: all 16 reached walks not\_run — the pinned Chromium lacks system libraries (libX11, libnss3, …) that need sudo to install, and this Hive has no swarm-replay user)
- python scripts/lint\_skills.py; python scripts/lint\_experts.py; bash scripts/sync-version.sh check → not\_run (not touched by this change (no skill, expert or version file changed); run through task\_checks after landing)

## Not run (the agent's reason, not checked)

- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_48eee90b: environment: all 16 reached walks not\_run — the pinned Chromium lacks system libraries (libX11, libnss3, …) that need sudo to install, and this Hive has no swarm-replay user
- swarmai · python scripts/lint\_skills.py; python scripts/lint\_experts.py; bash scripts/sync-version.sh check: not touched by this change (no skill, expert or version file changed); run through task\_checks after landing
