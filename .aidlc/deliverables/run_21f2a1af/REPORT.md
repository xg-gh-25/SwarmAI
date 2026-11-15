# # North star and Eval: trend calls within the noise, counts from records, a jud… · Audit through DELIVER

Delivered: # North star and Eval: trend calls within the noise, counts from records, a judge calibrated both ways \*\*Evidence (2026-11-15).\*\* 1. The trend is decided by two different rules: the prompt headline co. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 58m 51s wall · 58m 51s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 red at base · 4/4 findings closed (1 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 722k comparable · 553k input · 168k output · 50.1M cache reads, not counted · 50.8M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Knowledge: requirement recalled · 8 · design recalled · 7 · cited 1 (+4 found on demand) · searched the DDD 3×

## Decisions

- D1 · Which interval for the difference of two proportions? → Agresti-Caffo adjusted Wald (add one success and one failure to each window), z = 1.96. Why: never zero width at 0%/100%, good coverage at these sizes, simple enough to explain in one line (DESIGN\_SPEC, agent)
- D2 · What is too few for a trend? → either 4-week window with fewer than 10 judged sessions gives no direction. Why: the existing MIN\_POOLED rule of the cognition tab (decision a881d217), now in the backend (DESIGN\_SPEC, agent)
- D3 · How is a GC row's computed count deduplicated? → distinct sessions among the anchored corrections' own sessions (resolved from their cand marker) and the sessions of corrections whose repeat names an anchor; an anchor with no resolvable session counts once on its own; a repeat with no session counts once by its unit id. Why: one mistake recorded twice in a conversation is not a second occurrence (decision 8551d958); mirrors §9.1's one root once (DESIGN\_SPEC, agent)
- D4 · Before/after windows for an accepted rule? → equal windows: the days since acceptance after it, the same number of days before it, counted as distinct sessions repeating the row's anchors. Why: two counts over unequal spans are not comparable (DESIGN\_SPEC, agent)
- D5 · How many random slots and which sessions? → targeted (corrected, then long) capped at 7, the rest of the cap of 10 (at least 3) drawn from the other sessions with random.Random(fixed seed) over the sorted ids. Why: the task's example; filling unused targeted slots with random ones costs no extra judge calls and grows the unbiased layer (DESIGN\_SPEC, agent)
- D6 · Which sessions take a 'normal' label? → only random-layer sessions the judge scored normal; low-scored sessions of either layer still take labels as before. Why: the miss rate is only unbiased on a random draw (DESIGN\_SPEC, agent)
- D7 · What does 'at least 20 labels with both outcomes' mean? → 20 or more labels for the prompt version, with at least one on a low-scored and one on a normal-scored session (both rates defined). Why: the point is calibration both ways (DESIGN\_SPEC, agent)
- D8 · Where do accepted-rule before/after repeats show? → governance\_miner.accepted\_outcomes, returned by GET /eval/governance/pending as 'accepted' and printed in the north star detail; not a new UI panel. Why: the readers that exist; a new panel needs its own design pass (DESIGN\_SPEC, agent)
- D9 · Where does north\_star get the GC table path? → governance\_miner.workspace\_gc\_table(). Why: the CDF write scan flags any module that names a governed file and writes one; north\_star writes north\_star.json and must not name EVOLUTION.md (BUILD report · swarmai, agent)
- D10 · What does the C&amp;M rail show as its sessions and share? → the trend's four-week window (judged sessions, pooled share) with the verdict and the before share; an older snapshot without before\_rate shows no direction. Why: the rail's number and its verdict must describe the same window as the headline (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep each surface computing its own trend but share one interval formula (a Python and a TypeScript copy). Not taken: Two copies of the rule is exactly the drift the task removes; the task asks for one backend function both read.
- A3 · Compute GC counts from CDF claim\_support roots as design §9.1 first wrote. Not taken: The task names the review's recorded repeat (version\_vector.repeats, the anchor of the earlier correction) as the source, and that is what read\_corrections already uses for the repeat rate; one source for repeats keeps the GC count and the north star repeat rate on the same record.
- A4 · A plain Wald interval. Not taken: A window with 0% or 100% corrected gives a zero-width interval and calls any difference real; Agresti-Caffo never has zero width and is the standard small-sample fix.

Requirements analysis:

- missing\_edge (R1.1): A window with 0% or 100% corrected gives a zero-width Wald interval and would call any difference real; the interval uses the Agresti-Caffo adjustment (one success and one failure added to each window), so it never has zero width. → rewritten
- missing\_edge (R2.1): The same session can both hold an anchored correction and repeat it, or repeat it twice; counting by distinct session (a correction with no session counts alone) keeps one session from counting twice, as design §9.1 counts one root once. → rewritten
- missing\_edge (R3.4): 'At least 20 labels with both outcomes' is read as both judge outcomes (low and normal) labelled, so both rates exist. → rewritten
- missing\_edge (R2.3): Windows before and after acceptance are equally long (the days since acceptance), so the two counts are comparable. → rewritten

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 14/27 tool calls
- correctness · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 3077740e5636: 6 pass, 0 fail, 0 not run
- test · swarmai @ a3d7fc55db63: 11 pass, 0 fail, 0 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: pass
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: pass

TEST · swarmai:
- perl -e 'alarm 200; exec @ARGV' .venv/bin/python -m pytest tests/test\_cognition\_level.py tests/test\_north\_star.py tests/test\_governance\_miner.py tests/test\_governance\_api.py tests/test\_session\_quality → pass (162 tests: the change's tests and its contracts' readers)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- npm run build → pass
- bash scripts/sync-version.sh check → pass
- 2 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: only tests/test\_precommit\_uv\_lock.py fails (3 tests): \`uv\` is not on this machine's PATH (shutil.which returns None); the same file fails identically on the run's base tree. The other 81 manifest files, 858 tests, pass on this tree.

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/fixtures/north\_star\_trend\_wording.json
