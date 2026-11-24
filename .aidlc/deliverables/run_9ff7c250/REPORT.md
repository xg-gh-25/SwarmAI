# ## Goal: recall scope does not depend on one customer project's naming · Audit through DELIVER

Delivered: ## Goal: recall scope does not depend on one customer project's naming backend/core/ddd\_orchestrator.py (\~:131-140) hard-codes \`\_BUSINESS\_PROJECT\_SUFFIXES = {"salesintel", "biz", "isv"}\`: a project co. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 37m 48s wall · 37m 48s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 8 not run · 1/3 findings closed |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 274k comparable · 215k input · 58k output · 9.5M cache reads, not counted · 9.7M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 6 · cited 1 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · What happens when two Brains declare the same skill? → Attribute it to nobody and fall through to signal-2 / signal-3. Why: detect\_active\_project's docstring requires an unambiguous owner and the module is fail-closed (a wrong project is worse than none); falling through matches how an unknown Projects/ path is handled (DESIGN\_SPEC, agent)
- D2 · Where does the reader live? → In recall\_multi (\_declared\_skill\_owners / \_owns\_skill), deleting the orchestrator helper. Why: recall\_multi is the only reader; the orchestrator stopped using the prefix when staleness moved to bound repos; the task limits edits to the two named files, so ddd\_bindings is not touched (DESIGN\_SPEC, agent)
- D3 · Read governed\_assets through load\_bindings or raw YAML? → Raw yaml.safe\_load, like ddd\_bindings.governed\_repo\_roots and ddd\_job\_registry. Why: load\_bindings parses only the bindings: array and raises on a governed\_assets-only file (Brain entry e53b8a2b) (DESIGN\_SPEC, agent)
- D4 · Measured cost of reading bindings.yaml per candidate on each skill-path detection → No cache. Why: \~1.7 ms per project (34 ms for 20), only when the editor path is under backend/skills/, once per turn, off the event loop; a cache would add an invalidation path for a cost that does not matter (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Add a new top-level field (e.g. skill\_prefixes) to project-agent.yaml read through core.project\_agent. Not taken: project-agent.yaml describes how a Hive run does the Brain's work (intake, modes, validations), not which assets it governs; bindings.yaml governed\_assets kind skill-set already is the Brain's declaration of the skills it owns and the seeded Brains already use it, so a second field would be a second source of truth for the same fact.
- A3 · Derive ownership from the domain-skill registry manifest (Projects/&lt;X&gt;/4-capabilities/ folder scan). Not taken: Those skills live under Projects/&lt;X&gt;/, which signal-1(a) already maps by path; the backend/skills/ branch is exactly for skills that are NOT in the Brain's folder, so the folder scan cannot declare them.
- A4 · Keep the suffix rule as a fallback when nothing is declared. Not taken: The task's done-criterion is that no project name decides the behaviour; any name fallback keeps one customer's naming deciding everyone's recall.

Requirements analysis:

- missing\_edge (R1.4): The old loop returned the first project whose derived prefix matched; with declarations two Brains can claim one skill. The docstring already says the owner must be unambiguous, so ambiguity attributes nothing. → rewritten
- missing\_edge (R1.1): Brains that already declare skill-set members (the seeded SwarmAI Brain lists s\_swarm-build, s\_chat-brain-check, ...) newly get signal1\_skill\_path for those skills; they declare ownership, so this is the intended 'declaring project gets it'. Before this change such paths fell to signal-2 (the bound repo), which already resolved the same project. → confirmed
- missing\_edge (R1.3): Existing &lt;D&gt;\_SalesIntel/\_BIZ/\_ISV projects lose skill-path attribution until they add a skill-set asset with prefix s\_&lt;d&gt;-; signal-1 project path, signal-2 and signal-3 are unchanged for them. → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 63ad17dda325: 5 pass, 0 fail, 0 not run
- test · swarmai @ 8ce745331afd: 5 pass, 0 fail, 8 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not_run
  - experts-lint: not_run
  - desktop-typecheck: not_run
  - desktop-contract-lint: not_run
  - desktop-build: not_run
  - version-check: not_run
  - backend-bvt: pass
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- python -m pytest tests/test\_recall\_skill\_ownership.py tests/test\_recall\_multi.py tests/test\_ddd\_orchestrator.py -q --timeout=60 --maxfail=1000 → pass (75 passed (new ownership tests, TestDetectActiveProject, orchestrator tests))
- python scripts/smoke\_import.py → pass (695 modules import)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH findings)
- bash scripts/run\_bvt.sh → pass (1334 passed with uv on PATH as in CI (without uv on PATH test\_precommit\_uv\_lock fails identically on the base tree: shutil.which('uv') is None))
- "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_9ff7c250 → not\_run (environment: the installed SwarmAI on this Hive reads features.yaml schema 1, the repo's is schema 2; the change is backend recall detection with no UI surface)
- python scripts/lint\_skills.py → not\_run (no skill file changed)
- python scripts/lint\_experts.py → not\_run (no expert file changed)
- npx tsc --noEmit → not\_run (no desktop file changed)
- npm run lint:contract → not\_run (no desktop file changed)
- 3 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · "$SWARM\_BACKEND\_BIN" py -m scripts.feature\_map replay --affected --run run\_9ff7c250: environment: the installed SwarmAI on this Hive reads features.yaml schema 1, the repo's is schema 2; the change is backend recall detection with no UI surface
- swarmai · python scripts/lint\_skills.py: no skill file changed
- swarmai · python scripts/lint\_experts.py: no expert file changed
- swarmai · npx tsc --noEmit: no desktop file changed
- swarmai · npm run lint:contract: no desktop file changed
- swarmai · npm run build: no desktop file changed
- swarmai · bash scripts/sync-version.sh check: no version file changed
- swarmai · npx vitest run --reporter=verbose: no desktop file changed

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_recall\_skill\_ownership.py
