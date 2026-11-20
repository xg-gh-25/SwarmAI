# # Fix: the backend never downloads tree-sitter grammars inside its own process,… · Audit through DELIVER

Delivered: # Fix: the backend never downloads tree-sitter grammars inside its own process, in source mode too ## The defect (measured on a Hive, 2026-11-20, and confirmed in the code) \`backend/core/code\_intel/gr. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | WARN | 49m 33s wall · 49m 33s active · 116.3% budget |
| Quality | WARN | 1/1 verification · 2 not run · 1 red at base · 4/5 findings closed (9 reported; within 10 lines of one file they count once) (4 test-closed, confirmed by review) · 2 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 688k comparable · 531k input · 157k output · 31.1M cache reads, not counted · 31.8M billed · host-session responses in the run window · 99.7% budget |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 1 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · should a failing bundle step abort dev start / Hive install / Hive update? → no: warn and continue. Why: the backend works without grammars (regex path); a GitHub outage must not stop a developer or roll back a Hive, matching the Hive Node-check precedent (update only warns) (DESIGN\_SPEC, agent)
- D2 · how does re-running the bundle on every dev.sh start avoid a 25 MB download each time? → a stamp file with the library version inside the output dir; skip when it matches and the library, pointed at the dir, lists every language. Why: the library version decides grammar ABI; the completeness check is platform-aware because the library lists only its platform's library files (a macOS dir rsynced to a Linux Hive is rebuilt) (DESIGN\_SPEC, agent)
- D3 · how is a stalled download in the bundle script bounded? → signal.alarm with the default SIGALRM action (process killed by the OS) where available, default 300 s, --deadline to override. Why: the download holds the GIL so no Python timer can run; the OS-level default action still terminates the process; the measured stall never finishes, so this is a genuine hang bound (DESIGN\_SPEC, agent)
- D4 · should the Hive update keep backend/generated/tree\_sitter\_grammars across the rsync --delete? → yes, exclude it (and keep the developer's own copy out of update-hive.sh's upload). Why: the package never contains it (gitignored), so --delete would wipe it and force a re-download every update; the stamp check rebuilds it when the library version changes (DESIGN\_SPEC, agent)
- D5 · how does the live-AST parser test behave without grammars on disk? → skip each language whose grammar is not on disk, naming the bundle command. Why: no grammar on disk is now a legitimate state that takes the regex path; where grammars exist the ABI guard still asserts (DESIGN\_SPEC, agent)
- D6 · default bundle deadline → 600 s instead of the planned 300 s. Why: a slow download measured 125 s in the original incident; 300 s left too little margin for the build path, where a killed bundle fails the build (BUILD report · swarmai, agent)
- D7 · three code-intel tests asserted a live grammar with no skip guard (they downloaded one before) → skip guard like their \~90 sibling tests. Why: no grammar on disk is now a legitimate regex-path state; with grammars on disk they still assert (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · keep the in-process fallback but run the pre-warm in a subprocess or later. Not taken: parser.py and the verify-import probe reach the same get\_language; a GIL-holding native download blocks every thread wherever in the process it runs, so only refusing it closes the class
- A3 · configure the library with a cache\_dir and a download-disabled flag so it refuses itself. Not taken: the library (1.8.x/1.15.x PackConfig: cache\_dir, languages, groups) has no offline switch; get\_language always falls back to fetching the manifest
- A4 · in source mode extract missing grammars from a bundle already in the library cache. Not taken: extraction is the library's download path too (the same native call); keeping all fetching/extracting in the bundle script outside the server is the rule the packaged app already follows

Requirements analysis:

- missing\_edge (R2.3): dev.sh runs on every backend restart; re-running the bundle each time would re-download \~25 MB every start → rewritten
- missing\_edge (R2.3): a library upgrade (uv sync / pip install -e) with old-version grammar files on disk could load ABI-mismatched grammars; the stamp records the version they were built for and a mismatch rebuilds → rewritten
- missing\_edge (R2.2): the Hive update's rsync --delete would remove backend/generated/tree\_sitter\_grammars (it is gitignored and not in the package), forcing a re-download every update → rewritten
- missing\_edge (R2.2): the prebaked-AMI user data strips the venv segment byte-for-byte; the bundle step must live outside that segment and cd into the backend itself so prebaked Hives also run it → rewritten
- missing\_edge (R1.2): the library cache keeps the platform bundle next to extracted libs; a language in the bundle but not yet extracted is not listed by downloaded\_languages and is refused (extraction is the bundle script's job) → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/26 tool calls
- security · review r2: read 100% of its prompt · 6/25 tool calls
- correctness · review r2: read 100% of its prompt · 12/25 tool calls
- security · review r1: read 100% of its prompt · 2/25 tool calls
- correctness · review r1: read 100% of its prompt · 4/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 9fff5a6d9678: 8 pass, 0 fail, 0 not run
- test · swarmai @ 5c7d5d28edd4: 9 pass, 0 fail, 2 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- python -m pytest tests/test\_tree\_sitter\_grammars.py tests/test\_tree\_sitter\_bundle\_setup.py core/code\_intel/tests --timeout=100 --maxfail=1000 -rs → pass (grammars on disk (generated by the bundle script): 355 passed, 4 skipped)
- HOME=&lt;empty temp&gt; python -m pytest tests/test\_tree\_sitter\_grammars.py tests/test\_tree\_sitter\_bundle\_setup.py core/code\_intel/tests --timeout=100 --maxfail=1000 -rs (copy of this tree without generated → pass (258 passed, 101 skipped; the real-library test behind a stalled proxy ran and passed (refused, no connection))
- python -m pytest tests/test\_tree\_sitter\_grammars.py -k 'empty\_cache or real\_library or own\_cache\_holds' (new tests on the base tree) → pass (fail-to-pass: all 3 fail on the base (the real library blocks on the download behind the stalled proxy until the 60 s timeout), pass on this tree)
- health probe: backend main.py from source on a temp HOME behind an HTTPS proxy that never answers (GitHub unreachable), poll /health → pass (this tree: /health 200 after 5.6 s, no CONNECT github.com (proxy saw only boto3's IMDS PUT and an npm registry CONNECT from other startup code); base: CONNECT github.com:443, no /health within 90 s, i)
- python -m pytest tests/test\_hive\_tmp\_on\_disk.py tests/test\_hive\_provisioner.py tests/test\_hive\_hardening.py tests/test\_hive\_caddy\_binary\_update.py tests/test\_hive\_caddy\_health\_exemption.py tests/test\_ → pass (222 + 10 passed (run in BUILD; the review fixes since touched only the update poll constants and the shared step, whose tests are in test\_tree\_sitter\_bundle\_setup.py))
- python scripts/smoke\_import.py → pass (670 modules imported)
- python scripts/lint\_problem\_values.py → pass (ok)
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- python scripts/lint\_terms.py --staged (the change staged, then unstaged) → pass (added wording clean)
- PYTEST\_ADDOPTS=--maxfail=1000 bash scripts/run\_bvt.sh → pre\_existing (6 failures, all in files this change does not touch and all failing identically on the base tree: test\_cdf\_materializer\_static (core/feature\_map/ledger.py writers unlisted), test\_silent\_except\_baselin)
- 2 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · PYTEST\_ADDOPTS=--maxfail=1000 bash scripts/run\_bvt.sh: 6 failures, all in files this change does not touch and all failing identically on the base tree: test\_cdf\_materializer\_static (core/feature\_map/ledger.py writers unlisted), test\_silent\_except\_baseline x2 (core/feature\_map/files.py, core/run\_protocol/engine.py), test\_precommit\_uv\_lock x3 (temp-repo 

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit; npm run lint:contract; npm run build; npx vitest run: no desktop source changed (only the shell script desktop/scripts/dev-with-backend.sh, checked by bash -n and the setup tests)
- swarmai · python scripts/lint\_skills.py; python scripts/lint\_experts.py; bash scripts/sync-version.sh check: no skill, expert or version file changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_tree\_sitter\_bundle\_setup.py
