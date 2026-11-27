# Add a film-wide layer (runtime/film-layer.js + scripts/film.py), a scene-writer… · Audit through DELIVER

Delivered: Add a film-wide layer (runtime/film-layer.js + scripts/film.py), a scene-writer brief template with general rules moved into scene-contract.md, and scripts/derive.py plus a work.py --derived-from opti. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 52m 16s wall · 52m 16s active · no baseline yet (1/5 runs) |
| Quality | PASS | 1/1 verification · 5/5 findings closed (11 reported; within 10 lines of one file they count once) (4 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 1.0M comparable · 797k input · 239k output · 48.2M cache reads, not counted · 49.2M billed · $20.80 book cost · host-session responses in the run window · no baseline yet (1/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 1 (+1 found on demand) · searched the DDD 3×

## Decisions

- D1 · where the per-project layer settings live → a separate film.json at the project root (template in templates/film.json). Why: storyboard.json's schema forbids unknown keys and x- keys are documented as ignored notes; the schema is not this run's file (DESIGN\_SPEC, agent)
- D2 · how the layer gets a style's look without overriding it → runtime/film-styles.json maps each ported style to its own classes (exec-dark ed-kicker, ed-progress...) and its own CSS variables for accent/ink/muted/rule/font; film.json may override; an unmapped part uses neutral fl- classes whose colours and font are --fl-\* variables pointing at those tokens. Why: styles decide the look; a test checks every mapped class and variable exists in the style's css (DESIGN\_SPEC, agent)
- D3 · how a forgotten film.py re-run after tts.py is caught, given tts.py cannot be edited here → the layer throws a page error naming film.py when the scene's duration differs from film-data.js by more than half a frame; film.py --check exits 1 when the data is stale. Why: check.py and render.py already fail on page errors, so stale progress data cannot be rendered by accident (DESIGN\_SPEC, agent)
- D4 · what decides whether a reused scene's render is carried → the old .fp must equal render.fingerprint of the old project (old render current) and a digest of the scene's render inputs without its id (html, referenced files with film-data.js replaced by the scene's own slice, runtime, shared.css, scene data, fade tail) must be equal in both projects; then build mp4/tail are copied and render.fingerprint of the new project is written. Why: only an identical picture may be carried; render.py then skips it by its own rule (DESIGN\_SPEC, agent)
- D5 · when carrying runs → derive.py carries at creation for every scene whose inputs are already known, and derive.py --carry --to N repeats it after tts.py and film.py. Why: new or re-voiced scenes have no duration until tts.py, and the film data (and so the slices) depends on durations (DESIGN\_SPEC, agent)
- D6 · who registers the derived project with Pollinate → INSTRUCTIONS runs work.py adopt --kind video --derived-from &lt;old&gt;; derive.py prints that command; work.py records derived\_from. Why: skill scripts read and write only inside their project; the registry lives in the workspace (DESIGN\_SPEC, agent)
- D7 · where the text-hold reading speeds live → stated in scene-contract.md (the rule's home), reported for project-contract.md's owner. Why: project-contract.md is not this run's file (DESIGN\_SPEC, agent)
- D8 · how a scene whose page never reads FL.scene is compared in derive → its film-data slice keeps only what the layer draws (duration, kicker, progress, plate image, and parity when the plate is on), so a neighbour's title change does not block a carry; a page that reads FL.scene is compared on its whole entry. Why: the picture depends only on what is drawn; reading FL.scene is detected from the page text (BUILD report · swarmai, agent)
- D9 · the skill linter test in a Python without yaml → skip with a reason (the linter needs the backend environment). Why: the skill tests are also run with a plain python3 that has the render deps but not the backend's (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Copy the reference film's approach: a project-local template common.tmpl.js plus generator per film, with film-wide literals compiled into one JS file every scene includes. Not taken: it is what three films already rebuilt by hand; the look was baked into the template (one style's night office), and with one opaque file every derived scene fingerprints differently, so nothing can ever be carried over.
- A3 · Let render.py inject the film data into \_\_MV\_DATA and draw the progress bar at join time. Not taken: render.py, check.py, tts.py and scene-runtime.js are owned by other runs on this task; a join-time overlay would also bypass the per-scene check and preview.
- A4 · derive.py rewrites old build fingerprints by trusting the old .fp only (no input comparison). Not taken: a reused scene whose voice, duration, tail, style or film slice changed would silently keep a wrong picture.

Requirements analysis:

- missing\_edge (R1.2, R3.4): the progress bar of every scene depends on the film total, so a duration change anywhere changes the picture of every scene that shows the bar; render.py fingerprints the whole film-data.js, so all such scenes re-render, which is correct → rewritten
- missing\_edge (R3.4): render.py's fingerprint includes the scene id, so a reused scene at a new position never matches by itself; derive compares the scene's render inputs with the id taken out (HTML, referenced files, voice data, duration, fade tail, the scene's own film-data slice) and only then carries the old mp4 and writes the new fingerprint with render.py's own function → rewritten
- missing\_edge (R1.4): tts.py cannot be edited by this run, so the regeneration cannot be hooked into it; the stale-data guard in the layer plus film.py --check make a forgotten re-run fail loudly → rewritten
- missing\_edge (R2.2): the text-hold reading-speed numbers are new fixed numbers; project-contract.md (the single source for numbers) is owned by another run, so they are stated in scene-contract.md and the project-contract change is reported for its owner → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 4/26 tool calls
- correctness · review r1: read 100% of its prompt · 10/27 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 9/25 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ b818035d439e: 6 pass, 0 fail, 0 not run
- test · swarmai @ 1141be195b8c: 5 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: not run
  - backend-problem-values: not run
  - backend-security-scan: not run
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
- perl -e 'alarm 100; exec @ARGV or die' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_92962ce28dbe/repos/swarmai/backend/.venv/bin/python -m pytest skills/s\_motion-video/tests -q --timeout=60 → pass (52 passed, 3 browser tests skipped (no playwright in the backend venv))
- PLAYWRIGHT\_NODEJS\_PATH=/usr/local/bin/node perl -e 'alarm 110; exec @ARGV or die' /tmp/mvenv/bin/python -m pytest skills/s\_motion-video/tests -q --timeout=100 → pass (scratch venv with playwright + Chromium + ffmpeg: 54 passed, 1 skipped (lint needs yaml); template preview, stale data failing check.py, render after derive rendering only new/changed scenes)
- perl -e 'alarm 110; exec @ARGV or die' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_92962ce28dbe/repos/swarmai/backend/.venv/bin/python -m pytest skills/s\_pollinate/tests/test\_work.py skills/s\_pollinate → pass (48 passed)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_92962ce28dbe/repos/swarmai/backend/.venv/bin/python scripts/lint\_skills.py → pass (corpus clean)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_92962ce28dbe/repos/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass (all modules import)
