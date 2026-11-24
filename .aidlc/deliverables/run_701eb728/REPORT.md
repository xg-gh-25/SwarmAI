# Fix the extraction standard and the declaration path so a later step of a recor… · Audit through DELIVER

Delivered: Fix the extraction standard and the declaration path so a later step of a recorded event resolves to the earlier one, both event doors show the same closest events, the DDD unit standard is tightened,. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 39m 57s wall · 39m 57s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 2 not run · 4/6 findings closed (3 test-closed, confirmed by review) · 1 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 613k comparable · 464k input · 149k output · 38.4M cache reads, not counted · 39.0M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+4 found on demand)

## Decisions

- D1 · What does the decision step do with an event declared adds to a recorded event? → Resolve it to that event: reject already\_exists with same\_anchor naming the earlier event; write it (linked derived\_from) only when it also declares closes or overrides the checks keep. Why: The person's standard: a later step is adds to the earlier event, not a new one; only a written unit closes a gap (overrides.apply and gaps.close\_declared read state='written'), so a step that finishes a gap must still be written (DESIGN\_SPEC, agent)
- D2 · Which scope spellings name an existing project? → The name (case, spaces, - and \_ ignored as before), a Projects/ prefix, or the name followed by a separator: slash, backslash, colon, opening bracket, #, &gt;, a spaced dash, an en or em dash. Why: Each is the project's own name with a part after it; a bare hyphen stays part of a name (billing-service) and a bare subsystem name stays the agent's declaration (no guessing) (DESIGN\_SPEC, agent)
- D3 · Where does the closest-event ranking live? → relations\_v2.event\_entries / closest\_event\_entries / unit\_text, used by s7\_review.\_candidates and closest\_events (the run report). Why: Two doors that rank events differently disagree about what is already recorded (P8); month summaries are no event to declare against (DESIGN\_SPEC, agent)
- D4 · How are declarations a unit type cannot hold handled? → Logged at info with the unit number and relation, the unit stands as new. Why: Gate-0: the silent drop hid the adds bug; refusing the unit would lose a valid unit for a wrong declaration (DESIGN\_SPEC, agent)
- D5 · Compute the S7 event pool once per review or per unit? → Once per review (relations\_v2.event\_entries read before the unit loop), ranked with relations\_v2.candidates. Why: closest\_event\_entries re-reads TIMELINE per call; the review ranks many units against the same pool (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Deduplicate events at the writer by title similarity or a model check. Not taken: A threshold or a per-entry model check is a review layer the person ruled out and the DDD records as rejected (a similarity threshold for duplicate events); quality is made at extraction.
- A3 · Let an event override another session's event (replace the earlier event with the whole current statement). Not taken: TIMELINE is history: replacing the earlier milestone with the later one loses when the earlier step happened, and the own-session rule for event overrides is a recorded decision; adds keeps the earlier event as the one record.
- A4 · Route a unit whose scope names a subsystem (project:Swarm Out) to the session's project. Not taken: Recorded as rejected (the session fallback put a tenets document's decisions into SwarmAI's DDD); only a scope that names the project itself, followed by a separator, is that project.

Requirements analysis:

- missing\_edge (R2.2, R2.3): An event that adds to an earlier event and also finishes an open gap: the gap is closed only by a written unit (gaps.close\_declared and overrides.apply read state='written'), so dropping it would leave the gap open. → rewritten
- missing\_edge (R4.1, R4.3): A scope that only starts with a project's letters (project 'Swarm', scope 'Swarm Out') is not that project: only an explicit separator after the project's name (/, :, (, a dash with spaces) or a Projects/ prefix makes it one; a bare subsystem name stays the agent's declaration, as decided before (no guessing of subsystems). → rewritten
- missing\_edge (R1.1): The five duplicate pairs come from the person's machine; this Hive has no TIMELINE, so they are reproduced as fixture events among unrelated background events from the same domain. → rewritten
- missing\_edge (R1.3, R4.4): Gate-0: the same silent drop applies to every relation a type lacks; the Hive-lesson brain check resolves the scope with the same function, so the separator rule goes inside \_resolve\_project. → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 13/26 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 6a851b3c1cd3: 3 pass, 0 fail, 0 not run
- test · swarmai @ 24b7462d4451: 6 pass, 0 fail, 2 not run
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
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_701eb7280dd9/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_cdf\_extraction\_standard.py tests/test\_cdf\_timeline\_entry.py tests/test\_cdf\_extractio → pass (305 tests: the change's tests plus the exit-criteria suites; the new file fails on the base (no adds on events, no shared event pool, project scope lost))
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_701eb7280dd9/repos/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_701eb7280dd9/repos/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_701eb7280dd9/repos/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_701eb7280dd9/repos/swarmai/backend/.venv/bin/python scripts/lint\_skills.py → pass
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_701eb7280dd9/repos/swarmai/backend/.venv/bin/python scripts/lint\_experts.py → pass
- bash scripts/run\_bvt.sh → not\_run (run in full, it stops at tests/test\_precommit\_uv\_lock.py: uv is not on this shell's PATH (shutil.which('uv') is None), an environment fact unrelated to this change; the other 80 manifest files were ru)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_701eb7280dd9/repos/swarmai/backend/.venv/bin/python -m scripts.feature\_map replay --affected --run run\_701eb728 → not\_run (16 reached walks not run for an environment reason: the pinned Chromium cannot load its system libraries and this machine has no swarm-replay user (the installed SwarmAI reads features schema 1, the r)

## Not run (the agent's reason, not checked)

- swarmai · bash scripts/run\_bvt.sh: run in full, it stops at tests/test\_precommit\_uv\_lock.py: uv is not on this shell's PATH (shutil.which('uv') is None), an environment fact unrelated to this change; the other 80 manifest files were run with the script's own pytest flags: 1358 tests passed
- swarmai · /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_701eb7280dd9/repos/swarmai/backend/.venv/bin/python -m scripts.feature\_map replay --affected --run run\_701eb728: 16 reached walks not run for an environment reason: the pinned Chromium cannot load its system libraries and this machine has no swarm-replay user (the installed SwarmAI reads features schema 1, the repo's is 2)

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_cdf\_extraction\_standard.py
