# Add an exact key-text hold check to check.py fed by scene-runtime.js tween reco… · Audit through DELIVER

Delivered: Add an exact key-text hold check to check.py fed by scene-runtime.js tween records, plus a storyboard-only cue-timing warning, with the numbers as one contract rule. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 24m 55s wall · 24m 55s active · no baseline yet (1/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 3/4 findings closed (3 test-closed, confirmed by review) · CF-1d5b427a: no mutation check (documentation of an intended behaviour; no code changed) · +2 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 430k comparable · 318k input · 112k output · 12.8M cache reads, not counted · 13.3M billed · $7.34 book cost · host-session responses in the run window · no baseline yet (1/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · when is a text block readable → when its last revealing tween (on itself or an ancestor) lands, i.e. tween end. Why: matches the author's rule 'no block may land in the last 1.2 s'; conservative for eased fades (DESIGN\_SPEC, agent)
- D2 · which text counts → text inside a reveal target, visible (effective opacity &gt;= 0.5, not hidden, not clipped &gt;= 50 %, on screen) at the scene end, excluding #mv-caption, script/style and text inside a nested reveal target (own block). Why: captions have their own band; dim/ghost states end below 0.5; static frame-0 text is not revealed by the timeline. project-contract defines no label size floor, so none is applied (DESIGN\_SPEC, agent)
- D3 · one or two fail items for a block landing in the last 1.2 s → one item whose message names both the hold numbers and the late landing. Why: required hold is always &gt;= 1.5 s &gt; 1.2 s, so a second item would only duplicate (DESIGN\_SPEC, agent)
- D4 · storyboard mode → new --storyboard flag: storyboard checks + cue warnings only, no static scan, no stills, no missing-scene-html fail. Why: no storyboard-only mode existed; --no-render still fails on missing scene files (DESIGN\_SPEC, agent)
- D5 · storyboard hold reference point → spoken start of the cue word (first match, MV.cue normalisation) to the scene duration. Why: the same word MV.cue would anchor to; the element lands later, so the warning is a lower bound (DESIGN\_SPEC, agent)
- D6 · commit by hand → left uncommitted for the run's landing. Why: worktree gate: land makes the one commit (BUILD report · swarmai, agent)
- D7 · fail message → adds 'it must be readable by &lt;t&gt;s'. Why: gives the author the number to act on (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · check.py infers reveals in Python from raw tween vars (opacity from/to) without the DOM. Not taken: cannot see ancestor fades, stagger targets, final text of counters or ghost/dim states; not exact
- A3 · check.py opens its own Playwright page per scene to sample visibility every frame. Not taken: a second browser session per scene duplicates render.py plumbing (render.py is not ours to change) and costs a full-frame sweep; the tween list already says when reveals can happen
- A4 · a new window.\_\_mvTextBlocks() hook read by render.py. Not taken: render.py is owned by another run; preview\_scene only forwards \_\_mvTweens()

Requirements analysis:

- missing\_edge (R1.1): stagger tweens reveal several targets at different times, and an ancestor fade can gate a child's readability → rewritten
- missing\_edge (R1.4): project-contract defines no label size floor, so no size-based exclusion is applied → confirmed
- missing\_edge (R1.1): render.py may not be edited, so check.py only receives window.\_\_mvTweens() → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 10/26 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 1e41a442ae0d: 4 pass, 0 fail, 0 not run
- test · swarmai @ ef230f6bc2bb: 7 pass, 0 fail, 1 not run
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
- cd backend &amp;&amp; perl -e 'alarm 100; exec @ARGV or die' /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_a1af32582f65/repos/swarmai/backend/.venv/bin/python -m pytest skills/s\_motion-video/tests -q --timeout=6 → pass (44 passed, 5 browser tests skipped (no playwright in the repo venv))
- cd backend &amp;&amp; MV\_REQUIRE\_BROWSER=1 PYTHONPATH=&lt;dir holding only python playwright&gt; .venv/bin/python -m pytest skills/s\_motion-video/tests -q --timeout=60 → pass (49 passed with real Chromium)
- python scripts/smoke\_import.py → pass (exit 0)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (PASS, no new HIGH findings)
- python scripts/lint\_skills.py → pass (44 SKILL.md pass; no machine-local references)
- python scripts/lint\_experts.py → pass (exit 0)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_a1af3258 → not\_run (9 reached walks not run: the replay tool reports 'uv is not on PATH' in its own environment (uv is installed under \~/.local/bin); the walks are desktop chat features a skill-only change cannot reach)

## Not run (the agent's reason, not checked)

- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_a1af3258: 9 reached walks not run: the replay tool reports 'uv is not on PATH' in its own environment (uv is installed under \~/.local/bin); the walks are desktop chat features a skill-only change cannot reach

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/skills/s\_motion-video/tests/test\_check\_text\_hold.py
