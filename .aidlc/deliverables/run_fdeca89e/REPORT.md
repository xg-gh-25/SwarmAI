# Add an owner-agent route and CLI command that records a hand landing after git … · Audit through DELIVER

Delivered: Add an owner-agent route and CLI command that records a hand landing after git on this machine verifies the named commits are on the prepared branch, after its base, and cover the returned files; the. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 48m 32s wall · 48m 32s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 5 not run · 5/6 findings closed (16 reported; within 10 lines of one file they count once) (5 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 926k comparable · 726k input · 200k output · 35.2M cache reads, not counted · 36.1M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement none · 0 · design recalled · 7 · cited 0 (+3 found on demand) · searched the DDD 2×

## Decisions

- D1 · must the named commits touch every returned file or at least one → every returned file of the repository (git diff --name-only base..tip, uncapped) except paths named with --dropped; each named commit must also touch at least one returned file. Why: at least one lets a commit that only touches a shared file (a changelog, a version file) settle a run it never landed; --dropped keeps a deliberate partial landing possible and puts it in the record (DESIGN\_SPEC, agent)
- D2 · does a hand landing wait for the agent's accept like return-land does → no: it settles as accepted at once, like the person's manual-landed (judgment\_pending treats a land entry of kind by\_hand as judged). Why: the task says it settles exactly like the person's manual-landed; the agent landed and tested the change itself in its own worktree, so the landing is its judgment (DESIGN\_SPEC, agent)
- D3 · does land\_blocker (a declared check errored) apply → yes, the same as return\_land. Why: otherwise a conflict would be a way around an errored check; return-land checks it before the merge, so a run that reached a conflict passes it (DESIGN\_SPEC, agent)
- D4 · which repositories need a named commit → those whose return-prepare state is prepared; landed (or rejected) ones are already handled; a repository that brought commits with no state, or unsealed commits, is refused as not prepared here. Why: a multi-repository run may land one repository by return-land and conflict on another (DESIGN\_SPEC, agent)
- D5 · what does the person's manual landing show on the funnel → its land gate also carries hand true and reads 'Landed by hand by you' (no sha); the button and route are unchanged. Why: the same marker manual\_landed\_at drives both, and the card then tells the two apart truthfully (BUILD report · swarmai, agent)
- D6 · cap how many returned files may be dropped (a reviewer's suggestion) → no cap. Why: every file not dropped must hold the run's whole change, so an unrelated commit cannot pass; dropping is the agent's recorded decision (kept per repository in the settle record), and a count cap would be a number standing in for that judgment (TEST report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Open the existing manual-landed route to the agent with optional commits in its body. Not taken: the gate matches paths, not bodies: the person-only path would have to become owner-callable, so the person's unchecked button and the agent's checked command would share one route and one op; the task asks to keep the person's route unchanged
- A3 · Make the agent re-run return-land with a --hand flag that accepts the hand-landed commit as the landing. Not taken: return-land lands a prepared tip by merge-tree; mixing a recording of an already-landed commit into it changes the land state file's meaning (landed/landed\_tip) that commits\_here, verify\_revert and the funnel read

Requirements analysis:

- missing\_edge (R2.3): which files must the named commits touch: every returned file or at least one. Decided every returned file except those named with --dropped: 'at least one' lets a commit touching only a shared file (a changelog, a version file) settle a run it did not land; an explicit --dropped keeps a deliberate partial landing possible and recorded → rewritten
- missing\_edge (R1.1): a run with several repositories where one already landed through return-land and another conflicted: only repositories still in state prepared need a named commit; a landed one is already handled → rewritten
- missing\_edge (R1.1): a check the Brain declared errored on the run: return-land refuses the agent (land\_blocker); landed-by-hand applies the same blocker so a conflict cannot be used to bypass it → rewritten
- missing\_edge (R2.1, R2.2): a commit id given by the agent goes into git argv: only 7-64 hex characters are accepted, so it can never be read as an option → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 10/26 tool calls
- correctness · review r1: read 100% of its prompt · 16/26 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r2: read 100% of its prompt · 8/25 tool calls
- correctness · review r1: read 100% of its prompt · 5/25 tool calls
- security · review r1: read 100% of its prompt · 5/25 tool calls
- security · review r2: read 100% of its prompt · 5/26 tool calls
- security · review r1: read 100% of its prompt · 4/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 5fa23e3f58e5: 8 pass, 0 fail, 0 not run
- test · swarmai @ 5cbbfb82fbd9: 10 pass, 0 fail, 5 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: not_run
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: not_run
  - backend-bvt: not_run
  - frontend-run-frontend-tests: not_run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_fdeca89ef77e/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_landed\_by\_hand.py -q -p no:cacheprovider --timeout=60 → pass (31 tests: the hand landing, a merge of main into the run's side, and every refusal (commit not on main, older than the base, touching none, sharing one line, base-duplicate line, main's or a later kee)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_fdeca89ef77e/repos/swarmai/backend/.venv/bin/python -m pytest tests/test\_swarm\_out\_landed\_by\_hand.py tests/test\_swarm\_out\_actor.py tests/test\_swarm\_out\_ops.p → pass (468 tests: the change and its direct callers)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_fdeca89ef77e/repos/swarmai/backend/.venv/bin/python scripts/smoke\_import.py → pass (all modules import)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_fdeca89ef77e/repos/swarmai/backend/.venv/bin/python scripts/lint\_problem\_values.py → pass (loaders keep problem values)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_fdeca89ef77e/repos/swarmai/backend/.venv/bin/python scripts/security\_scan.py → pass (no new HIGH findings)
- /home/swarm/.swarm-ai/SwarmWS/Hive/work/so\_fdeca89ef77e/repos/swarmai/backend/.venv/bin/python scripts/lint\_skills.py → pass (SKILL.md edit passes)
- npx vitest run src/components/swarm-out/funnel.test.tsx → pass (9 tests incl. landed by hand (en, zh))
- npx tsc --noEmit → pass (typecheck)
- npm run lint:contract → pass (i18n contract)
- npm run build → pass (bundle within caps)
- 5 more in run_audit.json

## Not run (the agent's reason, not checked)

- swarmai · python -m scripts.feature\_map replay --affected --run run\_fdeca89e: runtime unavailable on this Hive: the pinned Chromium lacks system libraries (libX11, libnss3, ...) and there is no swarm-replay user; all 16 reached walks not\_run for that environment reason
- swarmai · python scripts/lint\_experts.py: no expert prompt or catalog file changed
- swarmai · bash scripts/sync-version.sh check: no version file changed; run through task\_checks after landing
- swarmai · bash scripts/run\_bvt.sh: run through task\_checks on the landed branch after DELIVER
- swarmai · npx vitest run --reporter=verbose: full frontend suite: run through task\_checks on the landed branch after DELIVER; the touched test file passes here

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_swarm\_out\_landed\_by\_hand.py
