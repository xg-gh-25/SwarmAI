# Replace the whole-film filter-graph re-encode in render.py join() with a split … · Audit through DELIVER

Delivered: Replace the whole-film filter-graph re-encode in render.py join() with a split join: scenes get a forced keyframe at the fade length, each fade is encoded as its own short segment from the tail and th. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 30m 55s wall · 30m 55s active · no baseline yet (1/5 runs) |
| Quality | WARN | 1/1 verification · 2 not run · 1/2 findings closed |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 419k comparable · 300k input · 119k output · 10.9M cache reads, not counted · 11.3M billed · $6.96 book cost · host-session responses in the run window · no baseline yet (1/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 · searched the DDD 2×

## Decisions

- D1 · A scene encoded before this change has no keyframe at frame k: re-render it or join the old way? → Neither in full: the join probes the receiving scene's real keyframes and extends that one fade segment to the scene's first keyframe at or after k (x264 puts one at most every 2 s), stream-copying the rest; it logs the scene and that a --force re-render gives the lossless split. Why: The file, not the fingerprint, is the truth at the consumer, so nothing can be mis-joined; re-rendering costs a full Chromium capture, the whole-film re-encode costs every frame; this costs at most 2 s of frames per such fade and stays correct for any encode (DESIGN\_SPEC, agent)
- D2 · Force the keyframe only in scenes that a fade leads into, or in every scene? → Every scene longer than k frames. Why: The encode then does not depend on the neighbour's transition, so switching a transition cut&lt;-&gt;fade never forces a re-render, and the fingerprint stays a function of the scene alone; the cost is one extra IDR per scene (DESIGN\_SPEC, agent)
- D3 · RENDER\_VERSION and the fingerprint → Unchanged. Why: The forced keyframe does not change what the scene shows; D1 handles encodes without it, so no mass re-render on upgrade (DESIGN\_SPEC, agent)
- D4 · When does the full re-encode join still run? → (a) the segments' codec parameters differ (profile, level, pix\_fmt, size, SAR, colour tags, timebase, frame rate or the H.264 parameter-set hash), e.g. scenes encoded by another ffmpeg/x264 build; (b) a fade leads into a scene not longer than the fade; (c) the split output fails verification. Each is logged with its reason. Why: Stream copy keeps the first file's parameter sets, so (a) would play wrong; (b) has no frames to split; (c) is a safety net that is never silent (DESIGN\_SPEC, agent)
- D5 · The old full re-encode join fails on ffmpeg 7.0 (setpts makes the link variable-rate; xfade refuses it) → Insert fps=&lt;fps&gt; after setpts in every input chain (and the same chain in the fade segments). Why: Frame timestamps are already on the 1/fps grid, so fps passes every frame through unchanged and only restores the constant frame rate xfade requires; this makes the fallback and the comparison baseline real (DESIGN\_SPEC, agent)
- D6 · concat in point rounding → the keyframe's own pts rounded to the nearest microsecond. Why: a round-up was tried and a mutation showed it changes nothing: the demuxer rescales to the nearest stream tick; the 24 fps test exercises an in point that is not a whole microsecond (BUILD report · swarmai, agent)
- D7 · tail clip with a wrong frame count → join refuses it with a re-render hint (exit 1) for both paths. Why: the fade segment and the old xfade both assume exactly k tail frames; before, a short tail silently shifted the film (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Encode each scene as two files (head = first k frames, body = the rest) so no inpoint seek is needed. Not taken: build/&lt;id&gt;.mp4 is a contract file other scripts read (defects.py, review\_evidence.py, check flows); two files per scene changes the project contract, which this task does not own.
- A3 · Keep the whole-film filter graph but use a faster preset. Not taken: still re-encodes every frame (second-generation loss on all frames) and changes the look of the film; it does not meet bit-identity outside fades.
- A4 · Bump RENDER\_VERSION so every old encode is re-rendered with the forced keyframe before the split join. Not taken: re-rendering is the expensive Chromium capture of every frame of every scene; probing the actual file for keyframes and re-encoding only up to the next existing keyframe keeps old builds correct at a few frames' cost.

Requirements analysis:

- missing\_edge (R2.1): the task offered re-render or old join for old encodes; extending only that fade's re-encoded run to the next keyframe keeps the film correct without re-rendering (the costly Chromium step) and without re-encoding the whole film → rewritten
- missing\_edge (R3.1): observed: the existing full re-encode join fails on ffmpeg 7.0.2 (setpts marks the link variable-rate and xfade refuses it), so the fallback and the comparison baseline must be fixed first → rewritten
- missing\_edge (R2.2): a fade into a scene that is not longer than the fade cannot be split; and segments with different codec parameters cannot be concatenated by stream copy (concat keeps the first file's parameter sets) → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 2321de435d9d: 5 pass, 0 fail, 0 not run
- test · swarmai @ ebfcf5ccc046: 6 pass, 0 fail, 2 not run
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
- LD\_LIBRARY\_PATH=$HOME/.local/syslibs/usr/lib64 perl -e 'alarm 100; exec @ARGV or die' .venv/bin/python -m pytest skills/s\_motion-video/tests -q --timeout=60 → pass (40 passed, none skipped (includes the Chromium-rendered 5-scene project and the 60 s timing test))
- python scripts/smoke\_import.py → pass (ok)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- python scripts/lint\_skills.py → pass (ok)
- python scripts/lint\_experts.py → pass (ok)
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_5e5e050b → not\_run (all 9 reached walks (chat/desktop app features, reached only through the catch-all 'app' target) were not run by the replay: 'uv is not on PATH' inside its sandbox although uv is installed in \~/.local)
- desktop-typecheck / desktop-contract-lint / desktop-build / frontend tests / version-check / backend-bvt → not\_run (the change touches no desktop, version or BVT-allowlisted file; each is run once through task\_checks on the landed branch before the task finishes)

## Not run (the agent's reason, not checked)

- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_5e5e050b: all 9 reached walks (chat/desktop app features, reached only through the catch-all 'app' target) were not run by the replay: 'uv is not on PATH' inside its sandbox although uv is installed in \~/.local/bin; environment reason, the change is a skill CLI script no walk exercises
- swarmai · desktop-typecheck / desktop-contract-lint / desktop-build / frontend tests / version-check / backend-bvt: the change touches no desktop, version or BVT-allowlisted file; each is run once through task\_checks on the landed branch before the task finishes

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/skills/s\_motion-video/tests/test\_join.py
