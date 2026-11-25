# Add work.py (work model, versions, revision guard, CLI) and work\_render.py (Pla… · Audit through DELIVER

Delivered: Add work.py (work model, versions, revision guard, CLI) and work\_render.py (Playwright renderer, font cache, contact sheet, fallback probe) to the s\_pollinate skill, following design section 13, with. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 1h 42m wall · 1h 42m active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 6 not run · 11/14 findings closed (18 reported; within 10 lines of one file they count once) (7 test-closed, confirmed by review) · CF-5d02ef95: no mutation check (the finding is about the tests themselves; the http\_get size cap and redirect check are mutated nowhere else, and the new test exercises both) · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.1M comparable · 857k input · 265k output · 36.8M cache reads, not counted · 37.9M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 1 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · How is a change outside the sections (head styles, scripts) guarded? → The frame (the source with each top-level section and the whitespace around it replaced by one marker, markers collapsed) is hashed under \_shared with the source file name as key. Why: A head style edit changes every slide while no slide hash changes; collapsing markers keeps adding, removing and moving slides from changing the frame (DESIGN\_SPEC, agent)
- D2 · What does moved mean when slides are added or removed? → Relative order of slides that exist in both base and new; a weighted longest common subsequence keeps as many out-of-scope slides as possible, the rest of the out-of-scope ones are moved. Why: Index comparison would call every slide after an inserted one moved (DESIGN\_SPEC, agent)
- D3 · What does a second open do while one is already open? → It updates scope, add, remove and note and keeps the base; it does not absorb. Why: Between open and cut the disk holds the agent's own work, which must not become a user version (DESIGN\_SPEC, agent)
- D4 · cut with no open when versions exist? → Exit 2 asking for open first; the first cut after init needs no open. Why: Without a declared scope the guard cannot tell intent, and absorbing user edits is open's job (DESIGN\_SPEC, agent)
- D5 · Are failed cuts stored? → Yes, as a version with status failed and its guard/checks/measure; current and open stay. Why: The evidence of why a version failed is kept; open stays so the agent can fix and cut again (DESIGN\_SPEC, agent)
- D6 · Name of the user version. → &lt;base&gt;-user, then -user2, -user3 when needed; current moves to the user version. Why: Matches the contract example (v003 -&gt; v003-user) and keeps current as the latest accepted state, so a second open does not absorb the same edits again (DESIGN\_SPEC, agent)
- D7 · How is an old version rendered again? → render --version V --out DIR, rendering .versions/V/src into DIR; each version records outputs.slides {id: sha256}. Why: The contract has no command for it, and out/ holds only the current version (DESIGN\_SPEC, agent)
- D8 · Which existing ids does adopt accept? → Generated ids follow s-&lt;4..8 hex&gt;; existing ids are kept when they are 1-64 chars of \[A-Za-z0-9\_-\], unique and not \_shared. Why: Rewriting an existing id would break 'change nothing else' (DESIGN\_SPEC, agent)
- D9 · Which files are \_shared? → Standard layout (source under src/): every file under src/ except the source; adopted source elsewhere: the local files the source references (through CSS too) inside the work dir. Why: An adopted folder can hold unrelated files (older decks) that must not be tracked or copied into snapshots (DESIGN\_SPEC, agent)
- D10 · Remote resources other than stylesheets. → Remote stylesheets (font CSS) are fetched once into the font cache; any other remote request is blocked during capture and reported as a failed check. Why: Deterministic offline output; the agent can download the file into src/assets/ (DESIGN\_SPEC, agent)
- D11 · How does cut call craft\_checks.evaluate? → once per slide measure, with canvas = work size, config = work.json 'craft', reference = the measure file named by work.json 'reference'. Why: 13.3 shows one measure per slide; P2 is built in parallel and its module is not here yet, so the built-in overflow probe runs (BUILD report · swarmai, agent)
- D12 · Where do a failed cut's outputs go? → review/failed-&lt;vid&gt;/ (out/ keeps the current version). Why: 13.1 says out/ holds only the current version; the agent still needs to see what failed (BUILD report · swarmai, agent)
- D13 · Extra keys added to the contract shapes → version entries carry slides (order), source and outputs.slides; guard.json carries notes (added more slides than declared); work.json may carry sizes, pptx, craft, reference. Why: needed for moved detection, re-render comparison and poster sizes; all are additive (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Parse the deck with a DOM library (BeautifulSoup/lxml) and serialize it back. Not taken: Serializing changes bytes (quotes, entity forms, whitespace), so adopt could not keep every byte outside the added attribute, and hashes would depend on the serializer; the libraries are also not guaranteed in the skill runtime.
- A3 · Ask Chromium for each section outerHTML and hash that. Not taken: The browser's serialization is normalized and depends on the Chromium version, so hashes would change on a browser update and a guard run would need a browser; hashing the source text is cheaper and stable.

Requirements analysis:

- missing\_edge (R3.3): A change to deck.html outside the sections (head styles) changes every slide but no slide hash. The frame (source with each slide replaced by one marker, whitespace between markers collapsed) is hashed under \_shared with the source file name. → rewritten
- missing\_edge (R2.1): open called again while an open already exists would absorb the agent's own work in progress as a user version. A second open only updates scope, add, remove and note and keeps the base. → rewritten
- missing\_edge (R3.2): cut with no open when versions exist cannot know the scope. It exits 2 and asks for open first; the first cut after init needs no open. → rewritten
- missing\_edge (R3.2): Inserting a slide shifts positions of others. moved is decided by relative order of surviving base slides (a longest common subsequence that prefers keeping out-of-scope slides), not by index. → rewritten
- missing\_edge (R5.2): The contract has no way to render an old version. render gets --version V --out DIR, and each version records its slide PNG hashes under outputs.slides. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 3/26 tool calls
- correctness · review r1: read 100% of its prompt · 12/27 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 17/25 tool calls
- security · review r1: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 11/25 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ ea6aae6a7b40: 8 pass, 0 fail, 1 not run
- test · swarmai @ f2ae0972ecce: 12 pass, 0 fail, 6 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: pass
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
- .venv/bin/python -m pytest -q -p no:cacheprovider skills/s\_pollinate/tests/test\_work.py → pass (37 passed)
- .venv/bin/python -m pytest -q -p no:cacheprovider skills/s\_pollinate/tests/test\_work\_guard.py → pass (10 passed (Chromium via user-space libraries on LD\_LIBRARY\_PATH))
- .venv/bin/python -m pytest -q -p no:cacheprovider skills/s\_pollinate/tests/test\_work\_render.py → pass (9 passed)
- .venv/bin/python -m pytest -q -p no:cacheprovider skills/s\_pollinate/tests/test\_work\_versions.py → pass (1 passed)
- backend/.venv/bin/python backend/scripts/generate\_app\_registry.py --check → pass
- SWARMAI\_SUITE=1 .venv/bin/python -m pytest -n 0 --timeout=60 -q -p no:cacheprovider tests/test\_app\_registry.py → pass (19 passed: the BVT file that checks the generated App index)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- 8 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: on this loaded host the full BVT needs about 30 minutes (34 percent after the 10-minute foreground limit, no failure so far); the change adds only skill files and the regenerated App index, whose BVT test file passes on its own
- swarmai · npx tsc --noEmit: no desktop file changed
- swarmai · npm run lint:contract: no desktop file changed
- swarmai · npm run build: no desktop file changed
- swarmai · npx vitest run --reporter=verbose: no desktop file changed
- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_d4db69ec: 16 reached walks not run for an environment reason: this host has no replay user and the pinned Chromium lacks system libraries

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/skills/s\_pollinate/scripts/work.py
- swarmai: backend/skills/s\_pollinate/scripts/work\_render.py
- swarmai: backend/skills/s\_pollinate/tests/fixtures/work/deck5/src/assets/photo.png
- swarmai: backend/skills/s\_pollinate/tests/fixtures/work/deck5/src/deck.html
- swarmai: backend/skills/s\_pollinate/tests/fixtures/work/deck5/src/shared.css
- swarmai: backend/skills/s\_pollinate/tests/fixtures/work/fonts/make\_test\_font.py
- swarmai: backend/skills/s\_pollinate/tests/fixtures/work/fonts/test-blocks.ttf
- swarmai: backend/skills/s\_pollinate/tests/test\_work.py
- swarmai: backend/skills/s\_pollinate/tests/test\_work\_common.py
- swarmai: backend/skills/s\_pollinate/tests/test\_work\_guard.py
- swarmai: backend/skills/s\_pollinate/tests/test\_work\_render.py
- swarmai: backend/skills/s\_pollinate/tests/test\_work\_versions.py
