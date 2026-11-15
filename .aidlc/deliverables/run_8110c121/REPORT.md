# # Recall: Chinese queries should find the English Brain · Audit through DELIVER

Delivered: # Recall: Chinese queries should find the English Brain \*\*Evidence (measured on a Hive, 2026-11-15).\*\* Ten Chinese queries in \`cognition find\` found their target entry in the top 8 zero times; the mat. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 51m 36s wall · 51m 36s active · no baseline yet (4/5 runs) |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 5/5 findings closed (4 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 657k comparable · 497k input · 160k output · 28.5M cache reads, not counted · 29.2M billed · host-session responses in the run window · no baseline yet (4/5 runs) |

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 3 (+3 found on demand) · searched the DDD 3×

## Decisions

- D1 · How are weights combined when a token appears more than once in the query? → Within one phrase a repeated bigram adds its shares up; across phrases or a whole word the larger weight wins. Why: A repeated query term was counted once before; max keeps that, and a whole word keeps weight 1 (DESIGN\_SPEC, agent)
- D2 · Which map keys count? → Keys with at least two CJK characters; Latin keys and one-character keys are ignored. Why: Substring matching of Latin or one-character keys false-fires (the recorded token-map lesson; 库 inside 数据库) (DESIGN\_SPEC, agent)
- D3 · How do overlapping keys match? → Longest first; a matched span is masked so a shorter key inside it does not fire. Why: 不一致 must not also add the terms of 一致 (DESIGN\_SPEC, agent)
- D4 · How does the chat DDD leg's coverage rule treat mapped terms? → An entry passes on the keywords' own coverage OR on the mapped English terms' coverage, each against its own required count. Why: Mixing the English terms into the keywords' count would raise the bar for Chinese entries; the map only adds hits (DESIGN\_SPEC, agent)
- D5 · Where is the SwarmAI Brain's own term map delivered? → As Outputs/so\_8110c1214954/term-map-SwarmAI.md for the person to append to Projects/SwarmAI/CONTEXT.md; the probe script takes --term-map to measure it before that. Why: On a worker Hive the Brain is a synced cache of the host's copy; an edit here is overwritten by the next sync and never reaches the person (DESIGN\_SPEC, agent)
- D6 · Where do the 10 probe pairs live? → In Outputs (the person's Brain-specific data), with a generic probe script in the repo and a fixture-Brain test of the script. Why: The pairs name entries of one person's Brain (fails the strip test for the product repo) (DESIGN\_SPEC, agent)
- D7 · Should the chat DDD leg pass its expanded query to recall\_all? → No: recall\_all's ddd leg expands its own query; the chat leg uses the mapped terms only for the coverage rule and the best entry of a section hit. Why: The mutation check showed the extra expansion changed nothing (idempotent), so it was removed (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep the map in a new top-level Brain file (TERMS.md). Not taken: brain\_sync's TOP\_FILES allowlist does not hold it, and an older Hive refuses a package member it does not accept ('the package has a member that is not accepted'), so a Brain with the new file would stop syncing to Hives of older releases. CONTEXT.md already travels and is not in the unit table.
- A3 · Keep the map under 2-understanding/knowledge/ or as Brain entries. Not taken: Those files are units of the Brain (addressing corpus, the ddd knowledge leg), so the map itself would be recalled as an answer to Chinese queries.
- A4 · Also weight the FTS5 legs (knowledge\_fts, messages\_fts) by turning a CJK term into a phrase query or re-ranking FTS rows in Python. Not taken: FTS5's bm25() takes only column weights. A phrase query changes what matches (adjacency required; the Brain records that the AND of bigrams is already too strict), and a Python re-rank of a LIMITed FTS result changes the leg's scoring model. Neither is the weighting asked for; the Python legs carry the measured failure.
- A5 · Expand the query once in recall\_all for every leg (library, session, code too). Not taken: The map is a Brain's vocabulary; applying one project's map to the cross-project Library, past sessions and the code graph widens the change beyond the Brain without evidence it helps there.

Requirements analysis:

- missing\_edge (R1.4): The SQLite FTS legs (library knowledge\_fts, session messages\_fts) also index CJK bigrams, but FTS5's bm25() takes no per-phrase weight: a MATCH can only weigh columns. Weighting them would need a Python re-rank of the FTS rows or a phrase query (which changes what matches). The weighting is applied to every leg that scores bigrams in Python; the FTS legs keep their scoring and this is stated in the report. → rewritten
- missing\_edge (R2.1): Where the map lives: a new top-level Brain file is not on brain\_sync's TOP\_FILES allowlist, and an older Hive refuses a package member it does not accept, so a new file would break Brain sync to older Hives. CONTEXT.md (the Brain's glossary) already travels and is not part of the unit table, so the map is a section of it. → rewritten
- missing\_edge (R2.1): A map key must contain a CJK character; Latin keys are ignored, because raw substring matching of Latin tokens false-fires (the recorded token-map lesson). Chinese keys match as substrings of the query's CJK text, which has no word boundaries. → rewritten
- missing\_edge (R3.2): The live Brain changes, so the repeatable test uses a fixture Brain built from real entries; the live 10-pair measurement is the probe script run before and after on this Hive's SwarmAI Brain. The SwarmAI Brain on a worker Hive is a synced cache, so its term map is delivered as a file in Outputs for the person to add to their Brain's CONTEXT.md. → rewritten

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 10/26 tool calls
- correctness · review r2: read 100% of its prompt · 11/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 45f1e51b0eab: 6 pass, 0 fail, 0 not run
- test · swarmai @ 9f56cfc018bf: 9 pass, 0 fail, 4 not run
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
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pre\_existing (861 tests; the only failures are the 3 tests of tests/test\_precommit\_uv\_lock.py, which fail the same way on the run's base tree (pipeline-v1 base-tree): no uv binary on this Hive's PATH (TypeError on )
- npx tsc --noEmit → not\_run (the change touches no desktop file; the task's recorded checks run after landing)
- npm run lint:contract → not\_run (the change touches no desktop file; the task's recorded checks run after landing)
- npm run build → not\_run (the change touches no desktop file; the task's recorded checks run after landing)
- 4 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 861 tests; the only failures are the 3 tests of tests/test\_precommit\_uv\_lock.py, which fail the same way on the run's base tree (pipeline-v1 base-tree): no uv binary on this Hive's PATH (TypeError on a None path)

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: the change touches no desktop file; the task's recorded checks run after landing
- swarmai · npm run lint:contract: the change touches no desktop file; the task's recorded checks run after landing
- swarmai · npm run build: the change touches no desktop file; the task's recorded checks run after landing
- swarmai · npx vitest run --reporter=verbose: the change touches no desktop file; the task's recorded checks run after landing

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/term\_map.py
- swarmai: backend/scripts/cjk\_recall\_probe.py
- swarmai: backend/tests/test\_bm25\_phrase\_weight.py
- swarmai: backend/tests/test\_cjk\_term\_map.py
