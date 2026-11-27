# Harden s\_motion-video for every user: one runtime contract with exit-2 hints, c… · Audit through DELIVER

Delivered: Harden s\_motion-video for every user: one runtime contract with exit-2 hints, cross-OS font stacks with bundled OFL CJK/hand fonts, SWARM\_WORKSPACE paths, a recorded GSAP licence decision, edge-tts te. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 47m 25s wall · 47m 25s active · no baseline yet (1/5 runs) |
| Quality | WARN | 1/1 verification · 6 not run · 0/3 findings closed (4 reported; within 10 lines of one file they count once) · review coverage: security r1 read 58% of its prompt |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 875k comparable · 668k input · 207k output · 45.3M cache reads, not counted · 46.2M billed · $18.56 book cost · host-session responses in the run window · no baseline yet (1/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 5 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which engine does a storyboard use? → --engine edge\|polly, else voice.engine, else the engine voice.name belongs to (a hyphenated name like zh-CN-YunxiNeural is edge, a single word like Zhiyu is Polly), else auto: Polly when credentials, a region and a one-request probe succeed, else edge. Why: Existing projects keep their voice on revision turns (a name never silently switches engine), while a new film gets the official engine whenever the person's AWS account allows it (DESIGN\_SPEC, agent)
- D2 · How does the cache key include the engine without re-voicing every existing edge project? → edge keeps the existing hash payload; other engines hash \[CACHE\_VERSION, engine, text, spoken, voice, rate\]. Why: Keys differ across engines, existing edge caches stay valid, and nothing is re-synthesised just because the code changed (DESIGN\_SPEC, agent)
- D3 · Where do bundled fonts go in a stack? → After the macOS families, before Windows/Linux system names and the generic family; one variable woff2 per family, GB2312 + Latin + punctuation subset. Why: macOS keeps the tuned look; Windows and Linux get the same bundled file, so a Linux render shows what Windows renders; characters outside the subset fall through to system CJK fonts (DESIGN\_SPEC, agent)
- D4 · What is the one supported way to run the scripts? → "$SWARM\_BACKEND\_BIN" py "$SKILL\_DIR/scripts/&lt;name&gt;.py"; python-deps \[boto3, PIL\] (bundled), python-optional-deps \[edge\_tts, playwright, numpy, jsonschema\]. Why: It is the product's interpreter on every OS (repo test test\_skill\_docs\_run\_python\_through\_py requires it); numpy and jsonschema are not backend dependencies yet, so declaring them bundled would be false (DESIGN\_SPEC, agent)
- D5 · Import botocore.config to set timeouts? → No: boto3 client with botocore's standard configuration. Why: botocore is not a declared backend dependency, so a direct import fails the repo's declared-imports test; defaults are adequate (BUILD report · swarmai, agent)
- D6 · Bundle Latin display faces too, not only CJK? → Yes: condensed, geometric, rounded, script, mono and hand Latin faces (small subsets). Why: Linux has no reliable equivalent and Windows has them only with Office; without them blueprint, hologram-hud, iso-infographic and game-show change width and look (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Cross-OS font stacks only (Windows and Linux system family names), no bundled fonts. Not taken: Linux distributions do not reliably carry any CJK serif or Kai face (this Hive has Noto Sans CJK only) and Windows ships Kai/Hei-black as optional features; layouts would still differ per machine, and nothing could be verified for Windows from Linux.
- A3 · Polly replaces edge-tts as the only engine. Not taken: Drops a working capability (task rule: improve, never drop) and leaves users without AWS credentials (a Bedrock API key user) with no voice.
- A4 · Fetch GSAP from npm on first use with a pinned sha256. Not taken: The current licence grants reproduce/display/implement for any website, web application or digital interface, its FAQ says AI-generated GSAP code is not a Prohibited Use, and the skill has no visual animation builder; a download step would add an offline failure mode for no licence gain.

Requirements analysis:

- missing\_edge (R5.4): Existing storyboards name an edge voice (zh-CN-YunxiNeural); switching them to Polly silently would mix two voices in one film on a revision turn. Resolution: the engine is inferred from voice.name when no engine is set (a hyphenated name is an edge voice), so existing projects stay on edge; auto applies only without a name or with --engine auto → rewritten
- missing\_edge (R5.4): Credentials can resolve while Polly is denied (this Hive's role has no polly:\* permission). Resolution: auto runs one tiny Polly speech-marks request as the probe and falls back to edge with the reason printed; an explicit polly engine that is denied exits 2 → rewritten
- conflict (R5.1): schemas/storyboard.schema.json has additionalProperties false on voice, so a storyboard with voice.engine fails check.py; the schema is not a file this task owns. Resolution: tts.py reads voice.engine; the schema change is listed for the owner in the final reply; --engine and the voice name work without it → rewritten
- missing\_edge (R1.1): The packaged backend does not carry Python Playwright or edge\_tts; render.py and check.py (not owned here) need Playwright. Resolution: SKILL.md states this plainly; the changes render.py/check.py/INSTRUCTIONS.md/bgm.py/sfx.py need are listed in the final reply → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 58% of its prompt · 9/27 tool calls
- correctness · review r1: read 100% of its prompt · 17/27 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ c72fb9225daa: 6 pass, 0 fail, 0 not run
- test · swarmai @ d68991810ef6: 8 pass, 0 fail, 6 not run
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
- perl -e 'alarm 100; exec @ARGV or die' .venv/bin/python -m pytest skills/s\_motion-video/tests -q --timeout=60 -p no:cacheprovider → pass (67 passed, 1 skipped (test\_fonts\_render: no Playwright in the backend venv))
- python -m pytest tests/test\_fonts\_render.py (Python with Playwright and Chromium) → pass (bundled faces draw the CJK and Latin text on Linux)
- python scripts/smoke\_import.py → pass (712 modules import)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- python scripts/lint\_skills.py → pass (44 SKILL.md pass; 755 corpus files clean)
- python scripts/lint\_experts.py → pass (13 expert files pass)
- bash scripts/sync-version.sh check → pass (all versions match)
- npx tsc --noEmit → not\_run (no desktop file changed; run through task\_checks on the landed commit)
- npm run lint:contract → not\_run (no desktop file changed; run through task\_checks on the landed commit)
- 4 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; run through task\_checks on the landed commit
- swarmai · npm run lint:contract: no desktop file changed; run through task\_checks on the landed commit
- swarmai · npm run build: no desktop file changed; run through task\_checks on the landed commit
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; run through task\_checks on the landed commit
- swarmai · bash scripts/run\_bvt.sh: run through task\_checks on the landed commit (recorded there)
- swarmai · py -m scripts.feature\_map replay --affected --run run\_d9eff24e: environment: the replay sandbox reports 'uv is not on PATH' for all 9 walks even with uv on the caller's PATH; the walks (chat, capabilities, health) are matched by a path rule, the change touches only s\_motion-video docs, styles, fonts, tts.py and tests
