# 按这个分层改设计文档；先做核心闭环，walk 用 Playwright spec 格式。Implement the Feature Map core loop… · Audit through DELIVER

Delivered: 按这个分层改设计文档；先做核心闭环，walk 用 Playwright spec 格式。Implement the Feature Map core loop per docs/design/2026-11-18-feature-map.md v3 (§14 steps 1-6, step 7 if the runtime spike passes; D1 §4.4, D2 §13; §10 ou. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | WARN | 10h 55m wall · 10h 55m active · 429.3% budget |
| Quality | WARN | 1/1 verification · 12/17 findings closed (20 reported; within 10 lines of one file they count once) (8 test-closed, confirmed by review) · review coverage: security r2 read 77% of its prompt · +1 more |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 1.3M comparable · 962k input · 344k output · 105.7M cache reads, not counted · 107.1M billed · host-session responses in the run window · 32.0% budget |

Project Agent: SwarmAI · code-change · 11 checks · 0 rules · 2 watches

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 2 (+5 found on demand) · searched the DDD 3×

## Decisions

- D1 · How is the pinned runtime installed and checked? → Node: official archive per platform, SHA-256 constants in core/feature\_map/runtime.py (from nodejs.org SHASUMS256 of the pinned version); @playwright/test: a package.json + package-lock.json shipped in the backend and installed with the pinned Node's npm ci (sha512 integrity from the lockfile); Chromium headless shell: the pinned Playwright's own installer into the runtime folder. Why: Same pattern as Caddy and mise (DDD 5a7cf9ce, 44800d06): pinned + checksum, downloaded on first use, not bundled. Chromium's archive has no checksum we can hold for every platform up front; integrity there rests on the pinned Playwright installer over HTTPS, said in the module docstring (DESIGN\_SPEC, agent)
- D2 · How does a spec resolve the pinned @playwright/test when the repo has its own? → The sandbox copy of the feature folder gets node\_modules/@playwright/test as a symlink to the pinned package; the CLI runs the pinned cli.js with a generated config. Why: Proven on macOS in THINK (spike /tmp/fmspike): a repo copy whose own @playwright/test throws was never loaded; Node resolves the nearest node\_modules and realpaths the symlink to the same module instance as the CLI (DESIGN\_SPEC, agent)
- D3 · How is the browser kept off the network? → Playwright use.proxy {server: http://127.0.0.1:9, bypass: 127.0.0.1,localhost,\[::1\]}. Why: Spike: Playwright proxies loopback by default (all pages failed with ERR\_PROXY\_CONNECTION\_FAILED until bypass was set); with the bypass, no-cors fetches to example.com and 1.1.1.1 were blocked and reached in the no-proxy control. DNS-only blocking was rejected: a literal IP bypasses it (DESIGN\_SPEC, agent)
- D4 · How is the tree a ledger line ran on matched to the run's code tree? → With --run, the CLI computes the worktree's code tree with core.run\_protocol.tree.build\_code\_trees (the engine's own function) and records it on each line; the engine matches lines by run id and that tree. Why: One function for both sides makes the match an identity, not a guess (R3 point 8) (DESIGN\_SPEC, agent)
- D5 · Where is the switch that reverts blocking (D2 of the design)? → A backend setting feature\_checks\_block (default true) read by the engine when it records the feature\_checks event and stored in its payload; FM-1 reads the payload. Why: Keeps FM-1 pure and the switch single; a run records which mode decided it (DESIGN\_SPEC, agent)
- D6 · Which platforms are verified in this run? → macOS on this machine; Linux in a Finch container if the image builds within the run; Windows only by code path (job object kill, env allowlist) and unit tests, said as not verified on a real Windows machine. Why: No Windows host here; the strip test needs the code path, the verification gap is said, not hidden (DESIGN\_SPEC, agent)
- D1 · Reuse the dependency links pipeline.toml declares inside the replay sandbox? → No: dependencies are installed fresh by the target's prepare, with the package caches passed through. Why: an npm ci through a link to the main checkout's node\_modules would wipe the person's own folder (BUILD report · swarmai, agent)
- D2 · How to keep the browser off the network while the app under test is on loopback? → A proxy at 127.0.0.1:9 that refuses every connection, with loopback (127.0.0.1, localhost, \[::1\]) bypassed. Why: Playwright proxies loopback too; without the bypass every page failed (seen in the spike). A no-cors control proved names and literal IPs are both refused (BUILD report · swarmai, agent)
- D3 · How is a quarantined walk left out of a replay? → Playwright's --grep-invert on the walk titles of the quarantined keys. Why: uses the runner's own selection; the walk still appears in the ledger as quarantined (BUILD report · swarmai, agent)
- D4 · Must a walk be green under verify before it may be saved? → A documented rule in the s\_feature-map writing standard, not a mechanical save gate. Why: the agent writes the files with ordinary edits; a gate on file writes would block native work (SOUL P7) (BUILD report · swarmai, agent)
- D5 · Archive extraction for the pinned Node → Every member checked first; tar extracted with filter='data', zip members one by one. Why: the bundled Python is 3.12, so the no-filter fallback was dead code; this also clears the scanner's B202 without a baseline entry (BUILD report · swarmai, agent)
- D6 · Windows and Linux → Written for both (process groups, taskkill /T, junctions, per-OS env allowlist, pinned archives), verified on macOS only. Why: no Windows or Linux machine here; listed as residual risk for REVIEW (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Our own YAML step format and a home-grown stepper (design v2.1). Not taken: Competes with Playwright for no gain and needs maintaining forever; the owner chose Playwright specs (2026-11-19).
- A3 · Let the agent report the feature replay as an ordinary Brain-check row in its TEST validation report. Not taken: Brain-check rows are the agent's own and never required (next\_step.py:323-329, validation\_report.py:285-287), so a red walk could land with a pass row; the decision must come from facts the CLI recorded.
- A4 · FM-1 reads the ledger file directly from checks.py. Not taken: Checks are pure evaluators over the run's events (checks.CheckContext); reading app-data files would make a check's verdict depend on state outside the run log. The engine records the facts as an event instead, like code\_trees.

Requirements analysis:

- missing\_edge (R1.3): Spec resolution of @playwright/test from a sandbox copy is unproven; it is the base the rest stands on. Handling: PLAN starts with a spike task that proves it on macOS and in Linux (Docker unavailable here: Linux and Windows via CI matrix or recorded as not verified on this machine) → rewritten
- missing\_edge (R3.3): DNS-only blocking is bypassed by a literal IP. Handling: rewritten: proxy that refuses every connection, loopback not proxied → rewritten
- missing\_edge (R7.3): pre\_existing needs a replay at the run's base. Handling: replay once in a base worktree only for walks failing at head → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 6/27 tool calls
- correctness · review r1: read 100% of its prompt · 17/28 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 77% of its prompt · 14/26 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 5/25 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ a0921b62392b: 11 pass, 0 fail, 0 not run
- test · swarmai @ c58942ae80d8: 12 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: not run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not run
  - backend-bvt: not run
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_feature\_map\_files.py tests/test\_feature\_map\_ledger.py tests/test\_feature\_map\_reach.py tests/test\_feature\_map\_runtime.py tests/test\_feature\_map\_pipeline.py tests/t → pass
- .venv/bin/python -m pytest -q --timeout=90 -p no:cacheprovider tests/test\_feature\_map\_pipeline.py tests/test\_feature\_map\_sandbox.py tests/test\_feature\_map\_cli.py → pass
- .venv/bin/python -m pytest tests/test\_feature\_map\_replay.py -q --timeout=250 -p no:cacheprovider -rf → pass
- .venv/bin/python -m pytest tests/test\_run\_protocol\_checks.py tests/test\_run\_protocol\_events.py tests/test\_run\_protocol\_audit.py tests/test\_pipeline\_run\_view.py tests/test\_pipeline\_v1\_conformance\_k4.py → pass
- npx vitest run src/components/layout/\_\_tests\_\_/PipelineRunCard.featureChecks.test.tsx → pass
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/security\_scan.py → pass
- npx tsc --noEmit → pass
- 2 more in run_audit.json

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/feature\_map/\_\_init\_\_.py
- swarmai: backend/core/feature\_map/files.py
- swarmai: backend/core/feature\_map/ledger.py
- swarmai: backend/core/feature\_map/pipeline.py
- swarmai: backend/core/feature\_map/reach.py
- swarmai: backend/core/feature\_map/replay.py
- swarmai: backend/core/feature\_map/runtime.py
- swarmai: backend/core/feature\_map/runtime\_lock/package-lock.json
- swarmai: backend/core/feature\_map/runtime\_lock/package.json
- swarmai: backend/core/feature\_map/sandbox.py
- swarmai: backend/scripts/feature\_map.py
- swarmai: backend/skills/s\_feature-map/INSTRUCTIONS.md
- swarmai: backend/skills/s\_feature-map/SKILL.md
- swarmai: backend/tests/feature\_map\_fixtures.py
- swarmai: backend/tests/test\_feature\_map\_cli.py
- swarmai: backend/tests/test\_feature\_map\_files.py
- swarmai: backend/tests/test\_feature\_map\_ledger.py
- swarmai: backend/tests/test\_feature\_map\_pipeline.py
- swarmai: backend/tests/test\_feature\_map\_reach.py
- swarmai: backend/tests/test\_feature\_map\_replay.py
- swarmai: 4 more in run_audit.json
