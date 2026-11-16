# # Fix: TIMELINE entries are construction logs, not events a later session needs · Audit through DELIVER

Delivered: # Fix: TIMELINE entries are construction logs, not events a later session needs \*\*Evidence (2026-11-16, the CDF judge over the last 7 days, \`scripts.north\_star\` detail).\*\* TIMELINE useful share is 70%. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 28m 42s wall · 28m 42s active · 29.6% budget |
| Quality | WARN | 1/1 verification · 4 not run · 5 red at base · 2/2 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 335k comparable · 264k input · 72k output · 15.0M cache reads, not counted · 15.3M billed · host-session responses in the run window · 25.2% budget |

Knowledge: requirement recalled · 5 · design recalled · 7 · cited 4 (+1 found on demand) · searched the DDD 3×

## Decisions

- D1 · Is a defect found and fixed inside one session ever an event? → Only when it reached beyond the work in progress (a person using the product, a running system, stored data): then it is an incident with its resolution; otherwise it is a step of the deliverable's one event. Why: The charter's incident is something that went wrong, not a build step; the judge's dominant failure was layout bugs found and fixed in the same session (DESIGN\_SPEC, agent)
- D2 · Does a deliverable finished in one session become a non-event under the step rule? → No: the rule cuts the steps inside a piece of work; what the work delivered stays one event (milestone). Why: Otherwise a landed feature delivered in a single session would vanish from the timeline (DESIGN\_SPEC, agent)
- D3 · Where does the key rule live so every door reads one copy? → A named sentence contracts\_v2.SAME\_WORK\_STEP composed into EVENT\_RULE; knowledge\_standard and summary\_lane state the same rule in their own sentence, tested for its presence. Why: knowledge\_standard must stay importable without ingestion (brain\_bootstrap reads it); the summary is phrased for a month, not a unit (DESIGN\_SPEC, agent)
- D4 · How are the judge's flagged entries assessed when cdf\_health.db is not on this Hive? → Deliver a read-only script that lists the flagged TIMELINE verdicts from cdf\_health.db on the person's machine, and assess the patterns the judge named (task evidence) against the new standard in the result. Why: The verdict store lives on the desktop; north\_star on this Hive scored 0 entries (DESIGN\_SPEC, agent)
- D5 · Should the judge's TIMELINE description (health.DESTINATIONS) change too? → No. Why: It is the measurement, already rates a step of a deliverable as not useful; changing it with the fix would make before/after incomparable (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Add a deterministic admission check that refuses events whose title or text looks like a same-session step (fixed, synced, rebuilt, status words). Not taken: A word list holds for one language and the phrases it knows and would be a review layer standing in for extraction quality; the project rejected exactly this (d4f06cf0, 708c83d5) and the task forbids a new gate.
- A3 · Change the judge's TIMELINE destination text in health.DESTINATIONS as well. Not taken: The judge is the measurement, not an extraction door; it already rates one step of a deliverable as not useful, and changing it with the fix would make the before/after share incomparable.

Requirements analysis:

- missing\_edge (R4.1): The judge's verdict store (cdf\_health.db) is on the person's machine, not on this Hive: north\_star here shows 0 scored. The flagged entries cannot be read in this session. → rewritten
- missing\_edge (R1.2): A deliverable finished in one session is still a milestone; the rule must cut the steps inside it, not the one event of what it delivered. → rewritten
- missing\_edge (R2.6): The judge's TIMELINE destination text (health.DESTINATIONS) is the measurement, not extraction; it already rates one step of a deliverable as not useful, so it is left unchanged to keep before/after comparable. → rewritten

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 12/25 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 055cac525771: 4 pass, 0 fail, 0 not run
- test · swarmai @ 74a7518baed2: 8 pass, 0 fail, 4 not run
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

TEST · swarmai:
- python -m pytest tests/test\_cdf\_timeline\_entry.py tests/test\_cdf\_launch\_review\_fixes.py tests/test\_cdf\_contracts\_v2.py tests/test\_cdf\_extraction\_structure.py tests/test\_cdf\_lesson\_compounding.py tests → pass (the change's own tests and every test that pins the standard text, exit 0)
- python -m pytest tests/test\_cdf\_\*.py tests/test\_ingestion\_hygiene.py -q --timeout=60 --maxfail=1000 (one run per file, 75 files) → pass (71 of 75 files green; the 4 files below fail the same tests on the run's base)
- python -m pytest tests/test\_cdf\_gap\_close.py -q --timeout=60 --maxfail=1000 → pre\_existing (test\_the\_run\_is\_shown\_its\_projects\_gaps\_and\_a\_landed\_report\_keeps\_closes fails identically on base a1d2b5689 (landing is None in the pipeline rig on this Hive))
- python -m pytest tests/test\_cdf\_materializer\_static.py -q --timeout=60 --maxfail=1000 → pre\_existing (materializer inventory drift (unlisted/stale writers in files this run does not touch) fails identically on base)
- python -m pytest tests/test\_cdf\_store\_and\_routing\_fixes.py -q --timeout=60 --maxfail=1000 → pre\_existing (test\_v11\_releases\_settled\_bytes... (sqlite freelist\_count) fails identically on base)
- python -m pytest tests/test\_cdf\_v2\_e2e.py -q --timeout=60 --maxfail=1000 → pre\_existing (the two test\_s3\_real\_pipeline\_\* tests (a real pipeline landing) fail identically on base)
- python scripts/smoke\_import.py → pass (exit 0)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (exit 0)
- python scripts/lint\_skills.py → pass (exit 0)
- 7 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · python -m pytest tests/test\_cdf\_gap\_close.py -q --timeout=60 --maxfail=1000: test\_the\_run\_is\_shown\_its\_projects\_gaps\_and\_a\_landed\_report\_keeps\_closes fails identically on base a1d2b5689 (landing is None in the pipeline rig on this Hive)
- swarmai · python -m pytest tests/test\_cdf\_materializer\_static.py -q --timeout=60 --maxfail=1000: materializer inventory drift (unlisted/stale writers in files this run does not touch) fails identically on base
- swarmai · python -m pytest tests/test\_cdf\_store\_and\_routing\_fixes.py -q --timeout=60 --maxfail=1000: test\_v11\_releases\_settled\_bytes... (sqlite freelist\_count) fails identically on base
- swarmai · python -m pytest tests/test\_cdf\_v2\_e2e.py -q --timeout=60 --maxfail=1000: the two test\_s3\_real\_pipeline\_\* tests (a real pipeline landing) fail identically on base
- swarmai · bash scripts/run\_bvt.sh: 858 of 861 pass; the 3 tests of test\_precommit\_uv\_lock.py fail because uv is not on this Hive's PATH (shutil.which('uv') is None), identically on base

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed; recorded through task\_checks on the task repository after landing
- swarmai · npm run lint:contract: no desktop file changed; recorded through task\_checks after landing
- swarmai · npm run build: no desktop file changed; recorded through task\_checks after landing
- swarmai · npx vitest run --reporter=verbose: no desktop file changed; recorded through task\_checks after landing
