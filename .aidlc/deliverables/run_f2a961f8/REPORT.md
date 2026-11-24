# ## Goal: text the backend shows to every user is in the user's language or plai… · Audit through DELIVER

Delivered: ## Goal: text the backend shows to every user is in the user's language or plain English, never fixed Chinese The backend already has a \`ui\_language\` setting (core/app\_config\_manager.py \~:113) that on. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 37m 15s wall · 37m 15s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 1 red at base · 1/2 findings closed (1 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 633k comparable · 499k input · 134k output · 31.4M cache reads, not counted · 32.0M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 3 (+2 found on demand) · searched the DDD 3×

## Decisions

- D1 · What language when ui\_language is empty or unreadable? → English. Why: the task says the user's language or plain English; empty means the app never said (DESIGN\_SPEC, agent)
- D2 · How is an old Chinese wake marker recognised when no code reads the marker today? → swarm\_out\_wake.is\_wake\_text knows MARKER, RUN\_MARKER and LEGACY\_MARKERS; core/run\_interventions' text rule (rows without a ledger record) uses it so a wake is never counted as the person steering. Why: that text rule is the only text reader of user rows that a wake reaches; the ledger covers rows written after it existed (DESIGN\_SPEC, agent)
- D3 · Feed labels (Weibo, East Money source/summary)? → plain English. Why: short labels; the content itself is the platform's (DESIGN\_SPEC, agent)
- D4 · OS Eval HTML report? → en/zh tables chosen once per report by ui\_language; the zh table keeps the current Chinese wording. Why: a long-lived user-facing file; follow-up items already carry en/zh (DESIGN\_SPEC, agent)
- D5 · mwinit hint in sources/common.py → English 'Run mwinit -f, then click Retry' only when the error names mwinit/Midway and aws\_account.remediation\_method() is the ADA path; else the generic sign-in-again hint. Why: mwinit is an internal tool; core/session\_unit already gates it the same way (DESIGN\_SPEC, agent)
- D6 · DEGENERATION\_TRANSCRIPT\_MARKER (read by the model only) → drop its Chinese half, keep the English. Why: no code matches the marker text (leak\_transcript only writes it), the English half already says everything, and it lets the module join the guard with no exemption (DESIGN\_SPEC, agent)
- D7 · Keep DEGENERATION\_NOTICE constant? → keep it as the English notice (channels/gateway reads it) and add degeneration\_notice() that picks by ui\_language for the chat path. Why: gateway is not in this task's file list (DESIGN\_SPEC, agent)
- D8 · English counts read '1 feeds' / 'watching 1 repos' → added \_one forms for counts of 1 (store detail, draft notes), zh tables carry the same keys. Why: the English text should read naturally; the table check keeps keys equal (BUILD report · swarmai, agent)
- D9 · the reply-subject regex in mail.py was inline → hoisted to \_REPLY\_PREFIX and exempted by name. Why: a named exemption instead of exempting the generic name msg (BUILD report · swarmai, agent)
- D10 · where the guard allows Chinese tables → a literal may hold CJK when assigned to a \*\_ZH name whose \*\_EN twin has the same keys and placeholders (test\_zh\_tables\_match\_en\_tables); regexes and legacy markers are named in EXCEPTIONS. Why: one rule for every pair instead of one EXCEPTIONS row per table, and the twin check makes the rule hard to abuse (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Return i18n keys to the frontend and translate there. Not taken: the task forbids editing frontend locale files, and the eval HTML report, chat notice and draft notes are written as text, not rendered from keys
- A3 · Plain English everywhere, no zh pairs. Not taken: Chinese users would lose readable Inbox cards and chat notices they have today; the task asks for the user's language where text is user-facing and long-lived
- A4 · Read ui\_language separately in each module (copy of inbox/runner.\_app\_language). Not taken: nine copies of the same try/except; one helper is the single reader and the unit tests patch one place

Requirements analysis:

- missing\_edge (R3.1): with an empty keyword list the old any() filter dropped every item; the filter must be skipped when there are no keywords → rewritten
- missing\_edge (R5.2, R5.3): no code reads the marker text today (wakes are recorded as system origin in the provenance ledger), but the run-intervention text rule for rows without a ledger row would count a wake as a correction; that rule is the reader that recognises both markers → rewritten
- missing\_edge (R1.1): where-labels of slack/github reply targets are stored at pull time in item meta; they follow the language at the time they were pulled → confirmed
- missing\_edge (R1.4): mwinit is an internal tool; it stays in the hint only on the ADA sign-in path, as core/session\_unit does → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 6/26 tool calls
- correctness · review r1: read 100% of its prompt · 20/27 tool calls
- correctness · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 3/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ f740fd1300de: 6 pass, 0 fail, 0 not run
- test · swarmai @ e4ddadc3fb58: 7 pass, 0 fail, 5 not run
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
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_ui\_language\_text.py tests/test\_backend\_fixed\_text\_english.py tests/test\_weibo\_eastmoney\_adapters.py tests/test\_inbox\_api.py tests/test\_inbox\_attention.py tests/te → pass
- bash scripts/run\_bvt.sh → pre\_existing (1331 of 1334 pass; the 3 tests of tests/test\_precommit\_uv\_lock.py fail the same way on the base tree: uv is not on PATH on this host (shutil.which('uv') is None))
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- npx tsc --noEmit → not\_run (no desktop file changed)
- npm run lint:contract → not\_run (no desktop file changed)
- 3 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 1331 of 1334 pass; the 3 tests of tests/test\_precommit\_uv\_lock.py fail the same way on the base tree: uv is not on PATH on this host (shutil.which('uv') is None)

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed
- swarmai · npm run lint:contract: no desktop file changed
- swarmai · npm run build: no desktop file changed
- swarmai · npx vitest run --reporter=verbose: no desktop file changed
- swarmai · python-backend py -m scripts.feature\_map replay --affected --run run\_f2a961f8: environment: 16 reached walks not run; the pinned Chromium lacks system libraries (libX11, libnss3, ...) and this host has no swarm-replay user; the installed python-backend also reads features.yaml schema 1 only

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/ui\_language.py
- swarmai: backend/tests/test\_ui\_language\_text.py
