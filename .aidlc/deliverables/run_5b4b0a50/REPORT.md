# # Feature Map: isolate start states in the replay engine · Audit through DELIVER

Delivered: # Feature Map: isolate start states in the replay engine Read the design first: \`docs/design/2026-11-20-feature-map-fake-model.md\` §4.2a (and §2's last bullet). Feature Map code: \`backend/core/feature. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | PASS | 45m 49s wall · 45m 49s active · 47.3% budget |
| Quality | WARN | 1/1 verification · 4 not run · 1 red at base · 3/4 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | PASS | 485k comparable · 373k input · 112k output · 17.4M cache reads, not counted · 17.9M billed · host-session responses in the run window · 36.4% budget |

Project Agent: SwarmAI · code-change · 11 checks · 1 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 3 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · Which services restart before a state? → Every running service of the sandbox is stopped; the target and the targets it needs are started again. Why: The state's setup usually writes to a needed target (an api); a service left running from an earlier target could carry its state (DESIGN\_SPEC, agent)
- D2 · Ports across restarts? → Ports are chosen once at prepare and kept; after a stop the engine waits briefly for each port to close before starting again. Why: A prepare step can bake a port into a build (SwarmAI's vite build reads VITE\_API\_URL with {port.api}) (DESIGN\_SPEC, agent)
- D3 · Which state do command walks run in? → default, together with the default web walks, after its setup. Why: They ran after the last web state on whatever its setup left; command walks have no @start tag, so default is their state (DESIGN\_SPEC, agent)
- D4 · What does {state\_dir} name in prepare, which runs before any state? → &lt;sandbox&gt;/state/.prepare, a folder no start state sees ('.' is not a state-name character). Why: The target env (which may use {state\_dir}) reaches prepare too; an unexpanded placeholder would create a literal folder (DESIGN\_SPEC, agent)
- D5 · A start that fails for one state? → The walks of that state and of all later states of the target are not\_run with the reason; a setup failure stays per state. Why: Services that did not start will not start next time either, and each retry would cost the ready timeout (DESIGN\_SPEC, agent)
- D6 · The folder path when two targets share a state name? → &lt;sandbox&gt;/state/&lt;state&gt; as the design defines it, recreated empty for each state run. Why: The services of the earlier target are stopped before, so nothing uses the old folder; follows the design's definition (DESIGN\_SPEC, agent)
- D7 · How to run the real-browser tests on a Hive without Chromium's system libraries and without sudo? → A copy of the pinned runtime in /tmp with the headless shell wrapped to add user-extracted libraries; set via a pytest plugin because tests/conftest.py strips inherited SWARM\_\* variables. Why: Verifies the product's behaviour without changing the person's runtime cache or the repo (BUILD report · swarmai, agent)
- D8 · Test names → Kept the plan's names (test\_enter\_state\_...) though the method is fresh\_start. Why: The plan's exit criteria name them (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Restart only the target's own services, and only before the second and later states. Not taken: A target that needs another (the INSTRUCTIONS example: web needs api, the state's setup writes to api) keeps the needed target's state across states, and a later target's first state runs on services an earlier target's walks changed: the same leak in another place.
- A3 · A second target per state, with its own services and data folder (design 4.2a option). Not taken: Needs a second UI build pointed at a second backend and leaves the leak for every other repo; the fix belongs in the engine where the leak is.
- A4 · A reset step in each state's setup. Not taken: Depends on every app having a full reset; SwarmAI's auth-method change keeps the stored secret.

Requirements analysis:

- missing\_edge (R1.1): A target that needs another target shares that target's services; restarting only the target's own services would leave the needed target's state (where setup usually writes, e.g. an api) leaking. And the first state of a later target would run on services an earlier target's walks used. → rewritten
- missing\_edge (R1.5): Command walks ran after the last web state, on whatever that state's setup left; they are given the default state. → rewritten
- missing\_edge (R1.1): Ports are kept across restarts: a prepare step can bake a port into a build (SwarmAI's vite build bakes VITE\_API\_URL with {port.api}). → rewritten
- missing\_edge (R1.4): Prepare runs once before any start state, so {state\_dir} there names a folder no start state sees (&lt;sandbox&gt;/state/.prepare). → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 6/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/26 tool calls
- correctness · review r2: read 100% of its prompt · 7/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 6c5492ae7da6: 5 pass, 0 fail, 0 not run
- test · swarmai @ 56a5ca895f13: 7 pass, 0 fail, 4 not run
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
- python -m pytest -p fmrt\_plugin tests/test\_feature\_map\_\*.py -v --timeout=500 --maxfail=1000 → pass (53 passed, the 6 real-browser replay tests included (Chromium run through a wrapped copy of the pinned runtime: this Hive lacks Chromium's system libraries and sudo is refused))
- python scripts/smoke\_import.py → pass (662 modules import)
- python scripts/lint\_problem\_values.py → pass (clean)
- python scripts/security\_scan.py → pass (no new HIGH findings, secrets or wildcard CORS)
- python scripts/lint\_skills.py → pass (44 SKILL.md files and the corpus pass)
- python scripts/lint\_experts.py → pass (10 expert files pass)
- bash scripts/sync-version.sh check → pass (all versions 2.5.5)
- bash scripts/run\_bvt.sh → pre\_existing (6 failures, every one also failing on the run's base tree (same run with --maxfail=1000 on both): test\_cdf\_materializer\_static (core/feature\_map/ledger.py writers not in the inventory), test\_silent\_ex)
- npx tsc --noEmit → not\_run (no desktop file changed)
- npm run lint:contract → not\_run (no desktop file changed)
- 2 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 6 failures, every one also failing on the run's base tree (same run with --maxfail=1000 on both): test\_cdf\_materializer\_static (core/feature\_map/ledger.py writers not in the inventory), test\_silent\_except\_baseline x2 (core/feature\_map/files.py and core/run\_protocol/engine.py, files this run does not

## Not run (the agent's reason, not checked)

- swarmai · npx tsc --noEmit: no desktop file changed
- swarmai · npm run lint:contract: no desktop file changed
- swarmai · npm run build: no desktop file changed
- swarmai · npx vitest run --reporter=verbose: no desktop file changed
