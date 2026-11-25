# # Pollinate v2 — P2: craft\_checks.py (measurable craft checks) · Audit through DELIVER

Delivered: # Pollinate v2 — P2: craft\_checks.py (measurable craft checks) ## Context (read first) SwarmAI is redesigning its content engine Pollinate (skill \`backend/skills/s\_pollinate/\` in the swarmai repo). Th. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 55m 58s wall · 55m 58s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 7 not run · 7/7 findings closed (11 reported; within 10 lines of one file they count once) (6 test-closed, confirmed by review) · review coverage: correctness r1 read 78% of its prompt · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 813k comparable · 625k input · 189k output · 24.4M cache reads, not counted · 25.2M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 1 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · What is the 'largest anchor' the font floor is a ratio of? → Two floors, the larger wins: a ratio of the canvas short side (body 0.022, label/caption 0.0167: 24 px and 18 px on 1080p) and a ratio of the largest title on the slide (body 0.2, label/caption 0.15). Without a title only the canvas floor applies. Why: Both come from the canvas, never a fixed px. The canvas floor catches a slide where everything is small; the title floor catches body text that looks tiny next to its heading. A huge stat number is not a title, so it does not push the floor up (DESIGN\_SPEC, agent)
- D2 · The 13.3 measure has no colors, but the palette part of the reference check needs them. → The probe adds an optional colors list per slide ({color, use: text\|fill, weight}) and color per text. Additive only; evaluate skips the palette when either side lacks colors. Why: The closest working version of the contract: every 13.3 key keeps its meaning, so P1 can store and pass the measure unchanged (DESIGN\_SPEC, agent)
- D3 · Which images count as content? → Images covering at least background\_area\_ratio (0.8) of the canvas, or with data-role=background, are backdrops: fill, overlap and safe\_area skip them. The probe records role only when declared. Why: A full-bleed backdrop would otherwise fill every slide and overlap every text (DESIGN\_SPEC, agent)
- D4 · How wide is an alignment slip? → A pair of sibling blocks that are stacked (or side by side) is a slip when its closest edge (left/center/right, or top/center/bottom) is off by more than align\_tol (0.3 % of the canvas width, at least 2 px) and at most align\_window (4 %). Larger offsets on every edge are treated as deliberate. Why: The user's 「对齐」 complaints are near misses; a centered title over a left list is a choice, not a slip (DESIGN\_SPEC, agent)
- D5 · How is density counted? → Weighted units: CJK and other wide characters count 2, other non-space characters 1; cap 400 units on 1920x1080, scaled by canvas area. Why: A wide character takes about two Latin widths of ink, so one cap fits Chinese and English slides (DESIGN\_SPEC, agent)
- D6 · What does the CLI print and accept? → JSON {ok, fails, warns, findings} on stdout; --measure accepts one slide measure, a list, or {slides: \[...\]}; optional --canvas WxH, otherwise the measure's w/h. Why: P1 stores a measure.json per version that may hold every slide; JSON output lets an agent read it (DESIGN\_SPEC, agent)
- D7 · How does the CLI treat a measure value of the wrong type? → Config values are type-checked in merge\_config; the CLI also maps TypeError to exit 2 ('invalid input'). Why: A traceback would exit 1, which means 'check failed' in the 13.3 exit codes (BUILD report · swarmai, agent)
- D8 · Where do the fixtures come from? → Measure JSON fixtures, each bad one changed from good.json in one way; bad\_reference.json is compared with good.json as the reference. Why: Deterministic on every machine; the browser path is covered by probe.html (BUILD report · swarmai, agent)
- D9 · How is a cover told apart from a too-empty content slide? → Only by declaration: data-layout=cover\|section\|quote\|stat on the slide (config sparse\_layouts); the probe records it as layout. An undeclared sparse slide fails fill and the message says how to mark a cover. Why: Any structural guess (no body text, few texts) also hides content slides that really are too empty, which is what fill is for (TEST report · swarmai, agent)
- D10 · Which sparse layout names are known by default? → cover, section, chapter, statement, quote, stat, hero, manifesto, closing, end, colophon (the shipped design systems' sparse slide types), compared without case. Why: Writing data-layout into templates belongs to other parts; the defaults match the names those templates already use (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Do the checks inside the browser (JS returns findings directly). Not taken: 13.3 fixes the split: the probe returns a measurement and evaluate() decides in Python, so P1 stores measure.json per version and a reference deck can be measured once and compared later. Checks in JS also cannot be tested without a browser.
- A3 · Make every per-check fixture an HTML page rendered through Playwright. Not taken: Line breaks and text widths depend on the fonts installed, so 'triggers only its own check' would differ between machines. Measure JSON fixtures make the check logic deterministic; the browser path is covered by its own probe test on a page with generous room.

Requirements analysis:

- missing\_edge (R2.7): The 13.3 measure shape has no colors, but the palette part of the reference check needs them. The probe adds an optional colors list per slide and a color per text; evaluate skips the palette when either side has no colors. → rewritten
- missing\_edge (R1.1): A deck stage often scales a slide with a CSS transform. The probe divides by the slide's scale so boxes stay in canvas px. → rewritten
- missing\_edge (R2.6): A full-bleed backdrop image would overlap every text and fill every slide. Images that cover at least background\_area\_ratio of the canvas, or have data-role=background, are backdrops and are skipped by fill, overlap and safe\_area. → rewritten
- missing\_edge (R2.3): Blocks that are deliberately far apart on every edge (a centered title over a left-aligned list) are not an alignment slip; only a near miss within the align window is reported. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 3/26 tool calls
- correctness · review r1: read 78% of its prompt · 8/26 tool calls
- correctness · review r2: read 100% of its prompt · 12/25 tool calls
- correctness · review r1: read 100% of its prompt · 10/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 0a471223734d: 6 pass, 0 fail, 0 not run
- test · swarmai @ 380182576e04: 10 pass, 0 fail, 7 not run
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


TEST · swarmai:
- perl -e 'alarm 100; exec @ARGV' /home/gawan/.swarmai-hive/data/SwarmWS/Hive/work/so\_93f89c6176c0/repos/swarmai/backend/.venv/bin/python -m pytest skills/s\_pollinate/tests/test\_craft\_checks.py -q --tim → pass (29 passed, 0 skipped; the two Playwright probe tests ran in Chromium)
- env -u LD\_LIBRARY\_PATH -u FONTCONFIG\_FILE -u PLAYWRIGHT\_NODEJS\_PATH perl -e 'alarm 100; exec @ARGV' /home/gawan/.swarmai-hive/data/SwarmWS/Hive/work/so\_93f89c6176c0/repos/swarmai/backend/.venv/bin/pyt → pass (27 passed, 2 skipped: without the browser libraries the probe tests skip, not error)
- perl -e 'alarm 110; exec @ARGV' .venv/bin/python -m pytest tests/test\_app\_registry.py -q --timeout=90 → pass (19 passed: the regenerated apps\_index.json matches the s\_pollinate package (the BVT runs this file))
- python backend/scripts/generate\_app\_registry.py --check → pass (generated App files are fresh)
- python -m scripts.mutation\_check mut\_final.json --timeout 90 -- perl -e 'alarm 100; exec @ARGV' /home/gawan/.swarmai-hive/data/SwarmWS/Hive/work/so\_93f89c6176c0/repos/swarmai/backend/.venv/bin/python  → pass (25 of 25 mutations caught earlier on this change, plus 4 for the second review visit and 1 for the chart-slide reference test (has\_image branch))
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass (PASS: no new HIGH findings, secrets, or wildcard CORS)
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- 7 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: the change has no desktop file; recorded through task\_checks on the delivered branch
- swarmai · npm run lint:contract: the change has no desktop file; recorded through task\_checks on the delivered branch
- swarmai · npm run build: the change has no desktop file; recorded through task\_checks on the delivered branch
- swarmai · bash scripts/sync-version.sh check: no version file changed; recorded through task\_checks on the delivered branch
- swarmai · bash scripts/run\_bvt.sh: the BVT manifest lists backend/tests only; this change adds a skill script and its own tests; recorded through task\_checks on the delivered branch
- swarmai · npx vitest run --reporter=verbose: no frontend file changed; recorded through task\_checks on the delivered branch
- swarmai · python -m scripts.feature\_map replay --affected --run run\_93f89c61: environment: all 16 reached walks not\_run (runtime unavailable): the pinned Feature Map Chromium lacks system libraries on this machine and there is no swarm-replay user (needs root)

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/skills/s\_pollinate/scripts/craft\_checks.py
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_align.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_density.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_fill.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_font\_floor.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_image.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_nowrap.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_overflow.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_overlap.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_reference.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/bad\_safe\_area.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/good.json
- swarmai: backend/skills/s\_pollinate/tests/fixtures/craft/probe.html
- swarmai: backend/skills/s\_pollinate/tests/test\_craft\_checks.py
