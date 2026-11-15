# # A Hive installs a new Brain version while turns still use the old one · Audit through DELIVER

Delivered: # A Hive installs a new Brain version while turns still use the old one \*\*Evidence (2026-11-15).\*\* Dispatching a second task of the same Brain to a Hive fails whenever the host's copy of the Brain cha. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 56m 58s wall · 56m 58s active · no baseline yet (0/5 runs) |
| Quality | PASS | 1/1 verification · 12/12 findings closed (8 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 910k comparable · 687k input · 223k output · 40.1M cache reads, not counted · 41.1M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 3 (+1 found on demand) · searched the DDD 2×

## Decisions

- D1 · Where does the version a turn reads come from in another process (the agent's cognition find, the pipeline CLI)? → A marker file per holding session under Hive/brains/&lt;brain&gt;/turns/, written by the backend before any file of the new version is written, read by brain\_versions.view with SWARM\_APP\_SESSION\_ID. Why: The CLI's env is fixed at spawn, so a per-turn env value is impossible; the session id is already in the env of every Hive session and a session has one open turn at a time. Off a Hive the store never exists, so view is one failed stat (DESIGN\_SPEC, agent)
- D2 · Which readers follow the turn's version? → addressing.brain\_root (find/get/map, run-engine citations), recall\_multi.\_load\_ddd\_docs\_text (DDD recall), the Brain gates (run\_gates, with the hook's session), and the run contract read when a run is created. Why: These are the readers that resolve the Brain by name during a turn. Writers (pipeline .artifacts, CDF journal, the install, make\_pin of a new session, the Hive-modified check) keep the stable path (DESIGN\_SPEC, agent)
- D3 · Domain skills and raw file reads → The projected .claude/skills and a raw Read of a Projects/&lt;brain&gt;/ path follow the newest version; this is stated in brain\_use's docstring and the design doc. Why: The CLI reads skills from the workspace-wide .claude/skills and cannot be given a per-turn skill set; the frequent changes are CDF writes to 2-understanding, which the resolved readers cover. A turn's enforced rules (the gates) do stay on its version (DESIGN\_SPEC, agent)
- D4 · A turn whose Brain cannot be told (ANY) → It no longer blocks installs: at an install of any Brain it is kept on that Brain's version current when it started, like a turn of that Brain. Why: Same guarantee (no turn reads a Brain changed under it) without the refusal the task removes (DESIGN\_SPEC, agent)
- D5 · How do overlapping host pushes stop hitting the remaining 423? → brain\_sync\_host.sync takes a per-(Hive, Brain) asyncio lock; a push that had to wait re-reads the Hive's Brains and skips when the Hive already has exactly this digest unchanged. Why: One process makes every host push (dispatch, console, defaults), so a lock serialises them; the re-read avoids a second identical install. The Hive keeps refusing two installs at once (DESIGN\_SPEC, agent)
- D6 · Disk work for kept versions → One dedicated single-thread executor pool (brain\_versions): copy, markers, removal, startup clear. Why: Off the event loop and off every pool the chat path waits on; one thread also orders the copy and the cleanups, so a cleanup never removes a copy being made (DESIGN\_SPEC, agent)
- D7 · Two packagings of one Brain at once on the host refused each other ('being written'), seen in the overlapping-push test → brain\_sync.collect serialises packagings of one Brain within the process (a threading lock per Brain dir). Why: Each packaging takes every document's .md.lock without waiting, so a second one read the first's lock as a writer's: two dispatches of one changed Brain would still fail without it. It is on the same dispatch path as the task, in the function this change already relies on (BUILD report · swarmai, agent)
- D8 · Skill re-projection on every install → Unchanged: every install still re-scans and projects the domain skills. Why: Not measured as a cost; skipping it when 4-capabilities is unchanged would be an optimisation without evidence (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Every version in its own directory and Projects/&lt;brain&gt; a symlink to the newest. Not taken: Projects/&lt;brain&gt; also holds Hive-local state that must not move between versions (.artifacts/ pipeline runs, code\_intel.db, cultivation writes); and brain\_dir, addressing.project\_root, brain\_sync.\_walk/install and the projection layer all refuse a symlinked Brain dir on purpose (planted-link defence), so every one of them would have to be loosened.
- A3 · Every version in its own directory, each turn told its directory, Projects/&lt;brain&gt; kept as an extra copy of the newest. Not taken: Two copies of every install on disk for the common case where no turn overlaps, a migration for Brains installed before, and an agent edit made in the version dir would be invisible to the Hive-modified check (current\_digest reads Projects/&lt;brain&gt;); it gains only path stability for raw Read calls of a path the agent got before the install.
- A4 · Defer the install until the holding turns end (stage B, swap later). Not taken: New turns would read A until every old turn ends, which is exactly the queueing the task removes.

Requirements analysis:

- missing\_edge (R1.2): A turn whose Brain cannot be told (its session record cannot be read) holds every Brain; it is kept on the version of each Brain current when it started, like any other turn, instead of blocking installs. → rewritten
- missing\_edge (R1.2): A new session's turn takes the Brain before its session id exists; the id is bound when the turn's session\_start arrives, and a marker is written then if its version was superseded meanwhile. → rewritten
- missing\_edge (R1.2, R1.3): The domain skills are projected into the workspace-wide .claude/skills and the agent's own direct Read of a Projects/&lt;brain&gt;/ path names the stable path: both follow the newest version. Readers that resolve the Brain (find/get/map, run knowledge, gates, contract) follow the turn. This boundary is stated in brain\_use's docstring and the design doc. → confirmed
- missing\_edge (R1.4): A session's next turn must not read a stale marker of its previous turn: a turn waits for the Brain's pending marker cleanup before it takes the Brain. → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 4/26 tool calls
- correctness · review r1: read 100% of its prompt · 12/26 tool calls
- security · review r2: read 100% of its prompt · 5/25 tool calls
- correctness · review r2: read 100% of its prompt · 8/25 tool calls
- security · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r1: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 3/25 tool calls
- security · review r2: read 100% of its prompt · 4/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 99e3247ecc88: 6 pass, 0 fail, 0 not run
- test · swarmai @ 64249eefa688: 8 pass, 0 fail, 0 not run
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
  - backend-bvt: pass
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- python -m pytest tests/test\_brain\_versions.py tests/test\_brain\_sync.py tests/test\_chat\_scenarios\_e2e.py -n 0 -q --timeout=60 → pass (the design's exit tests X1-X9 and the review's tests on the reviewed tree, with the chat turn scenarios (brain\_use.hold is on the turn path))
- python -m pytest tests/test\_addressing.py tests/test\_cognition.py tests/test\_cognition\_refs.py tests/test\_knowledge\_cli.py tests/test\_project\_agent\_session.py tests/test\_recall\_multi.py tests/test\_rec → pass (readers and host dispatch, run on the tree before the last brain\_use-only change (their code is unchanged since))
- python scripts/smoke\_import.py → pass
- bash scripts/run\_bvt.sh → pass (with the task's toolchain (uv) on PATH)
- python scripts/security\_scan.py → pass (run on the tree before the last brain\_use-only change)
- python scripts/lint\_problem\_values.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/brain\_versions.py
- swarmai: backend/tests/test\_brain\_versions.py
